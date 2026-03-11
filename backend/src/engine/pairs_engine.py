"""Statistical Arbitrage (Pairs Trading) Engine — Production Grade.

Strategy
--------
1. Warm up a rolling window of price observations for each pair.
2. Every ``REVALIDATION_INTERVAL`` ticks, run a full cointegration check
   (Engle-Granger + ADF + OU half-life).  A pair that fails is *disabled*
   — no new entries, existing positions are force-closed.
3. When |z-score| > entry_z (2.5 σ) AND the pair passes validation AND the
   risk manager allows the trade:
       z > +2.5  →  SHORT A, LONG B   (A over-priced vs B)
       z < −2.5  →  LONG A, SHORT B   (A under-priced vs B)
4. Position sizing uses the OLS hedge ratio β:
       qty_a  = notional / price_a
       qty_b  = (notional × β) / price_b    ← market-neutral sizing
5. Close when ANY of:
       |z| ≤ exit_z (0.3 σ)          → exit_signal  (profit target)
       |z| ≥ stop_loss_z (4.0 σ)     → stop_loss    (cut loss)
       hold time ≥ max_hold_days      → max_hold     (stale position)
       pair fails re-validation       → revalidation_fail
6. Real cost model (fees + funding + slippage) is applied to net P&L.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from ..db.session import get_session
from .pair_validator import ValidationResult, validate_pair
from .real_costs import RealCostModel
from .risk_manager import RiskManager

logger = logging.getLogger(__name__)

# Pairs tracked.  Both symbols must appear in symbols.json.
PAIRS_CONFIG: list[tuple[str, str]] = [
    ("ETH-USD", "SOL-USD"),
    ("BTC-USD", "ETH-USD"),
]

# Throttle: minimum seconds between successive state updates per pair.
_UPDATE_INTERVAL_S: float = 0.5

# Re-run cointegration after this many state-update ticks.
REVALIDATION_INTERVAL: int = 500   # ≈ every 4 min at 0.5 s/tick

# Number of ticks per 1-minute bar at 0.5 s/tick.
_TICKS_PER_MINUTE: int = 120  # 60 s / 0.5 s

# Minimum 1-minute bars required before running cointegration tests.
_MIN_BARS_FOR_VALIDATION: int = 60   # 1 hour of 1-min bars

# Pair must fail this many consecutive revalidations before being disabled.
# At REVALIDATION_INTERVAL=500 ticks (4 min) this means 12 min of bad data.
_MAX_CONSECUTIVE_FAILURES: int = 3


# --------------------------------------------------------------------------- #
# Data classes                                                                  #
# --------------------------------------------------------------------------- #

@dataclass
class PairsSignal:
    """Point-in-time signal snapshot."""

    pair_id: str
    symbol_a: str
    symbol_b: str
    price_a: float
    price_b: float
    spread: float
    z_score: float
    mean_spread: float
    std_spread: float
    correlation: float
    hedge_ratio: float      # OLS β used for position sizing
    half_life_hours: float
    signal: str             # "long_a_short_b"|"short_a_long_b"|"none"
    data_points: int
    timestamp: float = field(default_factory=time.time)


@dataclass
class PairsPosition:
    """In-memory state for one open pairs position."""

    pair_id: str
    symbol_a: str
    symbol_b: str
    direction: str          # "long_a_short_b" | "short_a_long_b"
    hedge_ratio: float
    entry_z_score: float
    entry_spread: float
    entry_price_a: float
    entry_price_b: float
    entry_time: float
    notional_usd: float     # dollar value of leg A
    qty_a: float            # units of A traded
    qty_b: float            # units of B traded  = notional * beta / price_b
    db_trade_id: Optional[int] = None

    @property
    def direction_a(self) -> int:
        return +1 if self.direction == "long_a_short_b" else -1

    @property
    def direction_b(self) -> int:
        return -1 if self.direction == "long_a_short_b" else +1

    def unrealized_pnl(self, price_a: float, price_b: float) -> float:
        pnl_a = self.direction_a * self.qty_a * (price_a - self.entry_price_a)
        pnl_b = self.direction_b * self.qty_b * (price_b - self.entry_price_b)
        return pnl_a + pnl_b

    @property
    def hold_hours(self) -> float:
        return (time.time() - self.entry_time) / 3600


# --------------------------------------------------------------------------- #
# Rolling state per pair                                                        #
# --------------------------------------------------------------------------- #

class PairsState:
    """Rolling price history for one asset pair.

    Two logical windows:
      z_window  — short window for the live z-score (fast signal).
      val_window — full history kept for cointegration tests.
    """

    def __init__(
        self,
        symbol_a: str,
        symbol_b: str,
        z_window: int = 60,
        val_window: int = 7200,   # 1 hour at 0.5 s/tick
    ) -> None:
        self.symbol_a = symbol_a
        self.symbol_b = symbol_b
        self.pair_id = f"{symbol_a}:{symbol_b}"
        self.z_window = z_window
        self.val_window = val_window

        self._prices_a: deque[float] = deque(maxlen=val_window)
        self._prices_b: deque[float] = deque(maxlen=val_window)

        self.validation: Optional[ValidationResult] = None
        self._tick_count: int = 0
        self.disabled: bool = False
        self.consecutive_failures: int = 0

    def update(self, price_a: float, price_b: float) -> None:
        if price_a <= 0 or price_b <= 0:
            return
        self._prices_a.append(price_a)
        self._prices_b.append(price_b)
        self._tick_count += 1

    def maybe_revalidate(self) -> bool:
        """Run cointegration test if due.  Returns True when newly run.

        Cointegration tests are designed for daily/hourly price series, not
        sub-second ticks.  Running them on raw 0.5 s data introduces severe
        microstructure noise and almost always produces spurious failures.

        Fix: downsample the stored tick series to 1-minute bars (every
        _TICKS_PER_MINUTE ticks) before calling validate_pair(), and require
        _MIN_BARS_FOR_VALIDATION bars (1 hour) before the first test fires.
        """
        if self._tick_count % REVALIDATION_INTERVAL != 0:
            return False

        # Downsample raw ticks → 1-minute bars
        raw_a = list(self._prices_a)
        raw_b = list(self._prices_b)
        pa = np.array(raw_a[::_TICKS_PER_MINUTE])
        pb = np.array(raw_b[::_TICKS_PER_MINUTE])

        if len(pa) < _MIN_BARS_FOR_VALIDATION:
            logger.debug(
                "Skip revalidation %s — only %d min-bars (need %d)",
                self.pair_id, len(pa), _MIN_BARS_FOR_VALIDATION,
            )
            return False

        # tick_interval_seconds=60 because each bar now represents 1 minute
        self.validation = validate_pair(pa, pb, tick_interval_seconds=60.0)
        logger.info(
            "Revalidated %s: valid=%s bars=%d p=%.3f β=%.3f t½=%.1fh — %s",
            self.pair_id, self.validation.is_valid, len(pa),
            self.validation.p_value, self.validation.hedge_ratio,
            self.validation.half_life_hours, self.validation.reason,
        )
        return True

    # ── Short-window statistics ──────────────────────────────────────────── #

    def _recent(self) -> tuple[list[float], list[float]]:
        pa = list(self._prices_a)[-self.z_window:]
        pb = list(self._prices_b)[-self.z_window:]
        return pa, pb

    @property
    def is_ready(self) -> bool:
        return len(self._prices_a) >= max(5, self.z_window // 2)

    @property
    def z_score(self) -> Optional[float]:
        if not self.is_ready:
            return None
        pa, pb = self._recent()
        spreads = [math.log(a / b) for a, b in zip(pa, pb) if a > 0 and b > 0]
        if len(spreads) < 5:
            return None
        arr = np.array(spreads)
        std = float(arr.std())
        if std < 1e-10:
            return 0.0
        return float((arr[-1] - arr.mean()) / std)

    @property
    def mean_spread(self) -> float:
        pa, pb = self._recent()
        if not pa:
            return 0.0
        vals = [math.log(a / b) for a, b in zip(pa, pb) if a > 0 and b > 0]
        return float(np.mean(vals)) if vals else 0.0

    @property
    def std_spread(self) -> float:
        pa, pb = self._recent()
        vals = [math.log(a / b) for a, b in zip(pa, pb) if a > 0 and b > 0]
        return float(np.std(vals)) if len(vals) > 1 else 0.0

    @property
    def correlation(self) -> float:
        pa, pb = self._recent()
        if len(pa) < 10:
            return 0.0
        try:
            c = float(np.corrcoef(pa, pb)[0, 1])
            return c if not math.isnan(c) else 0.0
        except Exception:
            return 0.0

    @property
    def current_spread(self) -> float:
        if not self._prices_a or not self._prices_b:
            return 0.0
        a, b = self._prices_a[-1], self._prices_b[-1]
        return math.log(a / b) if a > 0 and b > 0 else 0.0

    @property
    def current_prices(self) -> tuple[float, float]:
        if not self._prices_a:
            return 0.0, 0.0
        return float(self._prices_a[-1]), float(self._prices_b[-1])

    @property
    def hedge_ratio(self) -> float:
        """OLS β from the latest validation, or price-ratio fallback."""
        if self.validation and self.validation.hedge_ratio > 0:
            return self.validation.hedge_ratio
        if self._prices_a and self._prices_b:
            return float(np.mean(self._prices_a)) / float(np.mean(self._prices_b))
        return 1.0

    @property
    def half_life_hours(self) -> float:
        return self.validation.half_life_hours if self.validation else float("inf")

    @property
    def data_points(self) -> int:
        return len(self._prices_a)


# --------------------------------------------------------------------------- #
# Engine                                                                        #
# --------------------------------------------------------------------------- #

class PairsEngine:
    """Manages all pair states, validates them, and executes paper trades
    with full risk management and real cost accounting.

    Lifecycle:
        engine = PairsEngine(db_engine, risk_manager, ...)
        await engine.initialize()
        await engine.on_ticker(symbol, mid_price)   # from main loop
        status = engine.get_status()                 # from API
    """

    def __init__(
        self,
        db_engine,
        risk_manager: RiskManager,
        cost_model: Optional[RealCostModel] = None,
        alerts=None,
        initial_balance: float = 5_000.0,
        entry_z_threshold: float = 2.5,
        exit_z_threshold: float = 0.3,
        z_window: int = 60,
        val_window: int = 7200,   # 1 hour at 0.5 s/tick
        cooldown_seconds: float = 120.0,
    ) -> None:
        self.db_engine = db_engine
        self.risk = risk_manager
        self.costs = cost_model or RealCostModel()
        self.alerts = alerts

        self.balance = initial_balance
        self.initial_balance = initial_balance
        self.entry_z = entry_z_threshold
        self.exit_z = exit_z_threshold
        self.cooldown_seconds = cooldown_seconds

        self._states: dict[str, PairsState] = {
            f"{a}:{b}": PairsState(a, b, z_window, val_window)
            for a, b in PAIRS_CONFIG
        }
        self._positions: dict[str, PairsPosition] = {}
        self._latest_prices: dict[str, float] = {}
        self._last_state_update: dict[str, float] = {}
        self._last_trade_time: dict[str, float] = {}

        self.total_trades: int = 0
        self.total_profit: float = 0.0

    # ── Init ─────────────────────────────────────────────────────────────── #

    async def initialize(self) -> None:
        from ..db.models import Base, PairsTrade  # noqa: F401

        async with self.db_engine.begin() as conn:
            await conn.run_sync(
                lambda c: Base.metadata.create_all(
                    c, tables=[PairsTrade.__table__], checkfirst=True
                )
            )

        if self.alerts:
            await self.alerts.on_startup(list(self._states.keys()), self.balance)

        logger.info(
            "PairsEngine ready | pairs=%s | balance=$%.2f | entry_z=±%.1f | exit_z=±%.1f",
            list(self._states.keys()), self.balance, self.entry_z, self.exit_z,
        )

    # ── Ticker ingestion ─────────────────────────────────────────────────── #

    async def on_ticker(self, symbol: str, mid_price: float) -> None:
        if mid_price <= 0:
            return

        self._latest_prices[symbol] = mid_price
        now = time.time()

        for pair_id, state in self._states.items():
            if symbol not in (state.symbol_a, state.symbol_b):
                continue
            price_a = self._latest_prices.get(state.symbol_a)
            price_b = self._latest_prices.get(state.symbol_b)
            if not price_a or not price_b:
                continue

            if now - self._last_state_update.get(pair_id, 0) < _UPDATE_INTERVAL_S:
                continue
            self._last_state_update[pair_id] = now

            state.update(price_a, price_b)

            if state.maybe_revalidate():
                await self._on_revalidation(pair_id, state)

            if pair_id in self._positions:
                await self._check_open_position(pair_id, state)
                continue  # no entry signal while position is open

            if state.disabled or not state.is_ready:
                continue

            z = state.z_score
            if z is None:
                continue

            if z >= self.entry_z:
                await self._try_open(state, z, "short_a_long_b")
            elif z <= -self.entry_z:
                await self._try_open(state, z, "long_a_short_b")

    # ── Revalidation handler ─────────────────────────────────────────────── #

    async def _on_revalidation(self, pair_id: str, state: PairsState) -> None:
        if state.validation and not state.validation.is_valid:
            state.consecutive_failures += 1
            logger.warning(
                "Pair %s validation failed (%d/%d): %s",
                pair_id, state.consecutive_failures,
                _MAX_CONSECUTIVE_FAILURES, state.validation.reason,
            )
            # Only disable after N consecutive failures to avoid reacting to
            # a single noisy test result.
            if state.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                state.disabled = True
                logger.error(
                    "Pair %s disabled after %d consecutive failures.",
                    pair_id, state.consecutive_failures,
                )
                if self.alerts:
                    await self.alerts.on_pair_disabled(pair_id, state.validation.reason)
                pos = self._positions.get(pair_id)
                if pos:
                    pa, pb = state.current_prices
                    await self._close(pos, pa, pb, state.z_score or 0.0, "revalidation_fail")
        elif state.validation and state.validation.is_valid:
            if state.consecutive_failures > 0:
                logger.info(
                    "Pair %s recovered after %d failure(s) — re-enabled.",
                    pair_id, state.consecutive_failures,
                )
            state.consecutive_failures = 0
            state.disabled = False

    # ── In-position risk checks ───────────────────────────────────────────── #

    async def _check_open_position(self, pair_id: str, state: PairsState) -> None:
        pos = self._positions.get(pair_id)
        if not pos:
            return
        z = state.z_score or 0.0
        pa, pb = state.current_prices

        triggered, reason = self.risk.check_stop_loss(z, pos.direction, pos.entry_z_score)
        if triggered:
            await self._close(pos, pa, pb, z, reason)
            return

        triggered, reason = self.risk.check_max_hold(pos.entry_time)
        if triggered:
            await self._close(pos, pa, pb, z, reason)
            return

        if abs(z) <= self.exit_z:
            await self._close(pos, pa, pb, z, "exit_signal")

    # ── Open position ────────────────────────────────────────────────────── #

    async def _try_open(
        self, state: PairsState, z: float, direction: str
    ) -> None:
        pair_id = state.pair_id
        now = time.time()
        if now - self._last_trade_time.get(pair_id, 0) < self.cooldown_seconds:
            return

        allowed, reason = self.risk.can_open_trade(self.balance, len(self._positions))
        if not allowed:
            logger.debug("Entry blocked %s: %s", pair_id, reason)
            return

        notional = self.risk.position_size_usd(self.balance)
        if notional < 10:
            return

        pa, pb = state.current_prices
        beta = state.hedge_ratio
        qty_a = notional / pa
        qty_b = (notional * beta) / pb

        pos = PairsPosition(
            pair_id=pair_id,
            symbol_a=state.symbol_a,
            symbol_b=state.symbol_b,
            direction=direction,
            hedge_ratio=beta,
            entry_z_score=z,
            entry_spread=state.current_spread,
            entry_price_a=pa,
            entry_price_b=pb,
            entry_time=time.time(),
            notional_usd=notional,
            qty_a=qty_a,
            qty_b=qty_b,
        )

        from ..db.models import PairsTrade as DBPairsTrade

        try:
            rec = DBPairsTrade(
                pair_id=pair_id,
                symbol_a=state.symbol_a,
                symbol_b=state.symbol_b,
                direction=direction,
                entry_z_score=round(z, 4),
                entry_price_a=pa,
                entry_price_b=pb,
                entry_time=datetime.now(timezone.utc),
                notional_usd=notional,
                hedge_ratio=round(beta, 4),
                half_life_hours=(
                    round(state.half_life_hours, 1)
                    if state.half_life_hours < 1e9 else None
                ),
                qty_a=round(qty_a, 6),
                qty_b=round(qty_b, 6),
                status="open",
            )
            async with get_session(self.db_engine) as session:
                session.add(rec)
                await session.flush()
                pos.db_trade_id = rec.id
        except Exception as exc:
            logger.error("Failed to persist open pairs trade: %s", exc)

        self._positions[pair_id] = pos
        self._last_trade_time[pair_id] = now

        logger.info(
            "PAIRS OPEN  %s | %s | z=%+.2f | β=%.3f | "
            "A@$%.2f (%.5f) B@$%.2f (%.5f) | $%.0f/leg",
            pair_id, direction, z, beta, pa, qty_a, pb, qty_b, notional,
        )

        if self.alerts:
            await self.alerts.on_trade_open(
                pair_id, direction, z, notional, beta, state.half_life_hours
            )

    # ── Close position ────────────────────────────────────────────────────── #

    async def _close(
        self,
        pos: PairsPosition,
        price_a: float,
        price_b: float,
        exit_z: float,
        close_reason: str,
    ) -> None:
        from ..db.models import PairsTrade as DBPairsTrade
        from sqlalchemy import select as sa_select

        self._positions.pop(pos.pair_id, None)

        pnl_a = pos.direction_a * pos.qty_a * (price_a - pos.entry_price_a)
        pnl_b = pos.direction_b * pos.qty_b * (price_b - pos.entry_price_b)
        gross = pnl_a + pnl_b
        net = self.costs.net_pnl(gross, pos.notional_usd, pos.hold_hours)
        hold_s = pos.hold_hours * 3600

        self.balance += net
        self.total_trades += 1
        self.total_profit += net
        self.risk.update_capital(self.balance)

        if pos.db_trade_id is not None:
            try:
                async with get_session(self.db_engine) as session:
                    res = await session.execute(
                        sa_select(DBPairsTrade).where(
                            DBPairsTrade.id == pos.db_trade_id
                        )
                    )
                    rec = res.scalar_one_or_none()
                    if rec:
                        rec.exit_z_score = round(exit_z, 4)
                        rec.exit_price_a = price_a
                        rec.exit_price_b = price_b
                        rec.exit_time = datetime.now(timezone.utc)
                        rec.pnl_a = round(pnl_a, 4)
                        rec.pnl_b = round(pnl_b, 4)
                        rec.net_pnl = round(net, 4)
                        rec.hold_seconds = round(hold_s, 1)
                        rec.pairs_balance_after = round(self.balance, 2)
                        rec.close_reason = close_reason
                        rec.status = "closed"
            except Exception as exc:
                logger.error("Failed to persist close pairs trade: %s", exc)

        logger.info(
            "PAIRS CLOSE %s | z=%+.2f→%+.2f | %s | "
            "gross=$%+.2f cost=$%.2f net=$%+.2f | bal=$%.2f",
            pos.pair_id, pos.entry_z_score, exit_z, close_reason,
            gross, self.costs.cost_usd(pos.notional_usd, pos.hold_hours),
            net, self.balance,
        )

        if self.alerts:
            await self.alerts.on_trade_close(
                pos.pair_id, net, pos.hold_hours, close_reason, exit_z
            )

    # ── API status ────────────────────────────────────────────────────────── #

    def get_status(self) -> list[dict]:
        result = []
        for pair_id, state in self._states.items():
            pos = self._positions.get(pair_id)
            pa, pb = state.current_prices
            z = state.z_score
            val = state.validation

            pos_dict = None
            if pos and pa > 0 and pb > 0:
                pos_dict = {
                    "direction": pos.direction,
                    "entry_z_score": round(pos.entry_z_score, 4),
                    "hedge_ratio": round(pos.hedge_ratio, 4),
                    "entry_price_a": pos.entry_price_a,
                    "entry_price_b": pos.entry_price_b,
                    "qty_a": round(pos.qty_a, 6),
                    "qty_b": round(pos.qty_b, 6),
                    "notional_usd": pos.notional_usd,
                    "unrealized_pnl": round(pos.unrealized_pnl(pa, pb), 2),
                    "hold_seconds": round(time.time() - pos.entry_time, 1),
                }

            if not state.is_ready:
                sig_str = "warming_up"
            elif state.disabled:
                sig_str = "disabled"
            elif pos is not None:
                sig_str = "close" if z is not None and abs(z) <= self.exit_z else "holding"
            elif z is not None and z >= self.entry_z:
                sig_str = "short_a_long_b"
            elif z is not None and z <= -self.entry_z:
                sig_str = "long_a_short_b"
            else:
                sig_str = "none"

            result.append({
                "pair_id": pair_id,
                "symbol_a": state.symbol_a,
                "symbol_b": state.symbol_b,
                "disabled": state.disabled,
                "consecutive_failures": state.consecutive_failures,
                "is_ready": state.is_ready,
                "data_points": state.data_points,
                "z_window": state.z_window,
                "val_window": state.val_window,
                "z_score": round(z, 4) if z is not None else None,
                "spread": round(state.current_spread, 6),
                "mean_spread": round(state.mean_spread, 6),
                "std_spread": round(state.std_spread, 6),
                "correlation": round(state.correlation, 4),
                "validated": val is not None,
                "cointegrated": val.is_valid if val else None,
                "p_value": round(val.p_value, 4) if val else None,
                "hedge_ratio": round(state.hedge_ratio, 4),
                "half_life_hours": (
                    round(state.half_life_hours, 1)
                    if state.half_life_hours < 1e9 else None
                ),
                "signal": sig_str,
                "price_a": pa,
                "price_b": pb,
                "has_position": pos is not None,
                "position": pos_dict,
            })
        return result
