"""Event-driven backtest engine for pairs trading.

Replays historical close prices tick-by-tick through the same z-score /
cointegration logic used by the live PairsEngine so results are apples-to-
apples with live trading.

Usage (from repo root):
    python -m backend.backtest.engine \
        --a  backend/backtest/data/ETHUSDT_1h.csv \
        --b  backend/backtest/data/SOLUSDT_1h.csv \
        --initial-balance 5000 \
        --entry-z 2.5 --exit-z 0.3

Output (printed + written to backtest/results/):
    - Per-trade log (CSV)
    - Equity curve (CSV)
    - Summary statistics
"""

from __future__ import annotations

import argparse
import csv
import logging
import time as _time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

# ---------------------------------------------------------------------------
# Cost model (matches real_costs.py)
# ---------------------------------------------------------------------------

TAKER_FEE_PCT = 0.0005   # 0.05 % per exchange per leg
SLIPPAGE_PCT = 0.0002    # 0.02 % per leg
FUNDING_RATE_PCT = 0.0001  # 0.01 % per 8 h


def round_trip_cost_pct(hold_hours: float) -> float:
    open_fees = 2 * TAKER_FEE_PCT
    close_fees = 2 * TAKER_FEE_PCT
    slippage = 4 * SLIPPAGE_PCT
    funding = max(0, (hold_hours / 8)) * FUNDING_RATE_PCT
    return open_fees + close_fees + slippage + funding


def net_pnl(gross_pnl_usd: float, notional_usd: float, hold_hours: float) -> float:
    cost = round_trip_cost_pct(hold_hours) * notional_usd
    return gross_pnl_usd - cost


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Tick:
    timestamp: str
    price_a: float
    price_b: float


@dataclass
class BacktestTrade:
    pair: str
    direction: str          # "long_a" | "long_b"
    entry_ts: str
    exit_ts: str
    entry_price_a: float
    entry_price_b: float
    exit_price_a: float
    exit_price_b: float
    hedge_ratio: float
    notional_usd: float
    qty_a: float
    qty_b: float
    entry_z: float
    exit_z: float
    gross_pnl: float
    cost_usd: float
    net_pnl: float
    hold_hours: float
    close_reason: str


@dataclass
class BacktestState:
    """Rolling windows + position tracker for one pair."""
    z_window: int = 60
    val_window: int = 300

    log_ratios: deque = field(default_factory=lambda: deque())

    # Cointegration params (refreshed periodically)
    hedge_ratio: float = 1.0
    spread_mean: float = 0.0
    spread_std: float = 1.0
    is_valid: bool = False
    ticks_since_validation: int = 0
    revalidation_interval: int = 500

    # Open position
    position: Optional["BacktestPosition"] = None
    cooldown_until: float = 0.0

    def push(self, price_a: float, price_b: float) -> None:
        ratio = np.log(price_a / price_b)
        self.log_ratios.append(ratio)
        if len(self.log_ratios) > self.val_window:
            self.log_ratios.popleft()

    def z_score(self) -> Optional[float]:
        if len(self.log_ratios) < max(5, self.z_window // 2):
            return None
        window = list(self.log_ratios)[-self.z_window:]
        arr = np.array(window)
        spread = arr[-1] - self.hedge_ratio * np.mean(arr)  # approximation
        if self.spread_std < 1e-10:
            return None
        return (arr[-1] - self.spread_mean) / self.spread_std

    def maybe_revalidate(
        self,
        prices_a: list[float],
        prices_b: list[float],
        interval_hours: float,
    ) -> None:
        self.ticks_since_validation += 1
        if self.ticks_since_validation < self.revalidation_interval:
            return
        if len(prices_a) < self.val_window:
            return

        self.ticks_since_validation = 0
        try:
            from statsmodels.tsa.stattools import coint, adfuller
            import statsmodels.api as sm
        except ImportError:
            self.is_valid = True  # skip if not available
            return

        n = self.val_window
        log_a = np.log(np.array(prices_a[-n:], dtype=float))
        log_b = np.log(np.array(prices_b[-n:], dtype=float))

        # OLS hedge ratio
        X = sm.add_constant(log_b)
        result = sm.OLS(log_a, X).fit()
        beta = float(result.params[1])

        # Engle-Granger cointegration
        _, p_coint, _ = coint(log_a, log_b)

        # Spread stationarity
        spread = log_a - beta * log_b
        p_adf = float(adfuller(spread, autolag="AIC")[1])

        # Half-life
        delta = np.diff(spread)
        lagged = spread[:-1]
        X2 = sm.add_constant(lagged)
        res2 = sm.OLS(delta, X2).fit()
        kappa = -res2.params[1]
        hl_hours = (np.log(2) / kappa * interval_hours) if kappa > 0 else float("inf")

        if p_coint < 0.05 and p_adf < 0.05 and 2 <= hl_hours <= 168:
            self.is_valid = True
            self.hedge_ratio = beta
            self.spread_mean = float(np.mean(spread))
            self.spread_std = float(np.std(spread))
        else:
            self.is_valid = False


@dataclass
class BacktestPosition:
    direction: str
    entry_ts: str
    entry_price_a: float
    entry_price_b: float
    entry_z: float
    hedge_ratio: float
    notional_usd: float
    qty_a: float
    qty_b: float
    entry_time_index: int


# ---------------------------------------------------------------------------
# Backtest engine
# ---------------------------------------------------------------------------

class BacktestEngine:
    def __init__(
        self,
        symbol_a: str,
        symbol_b: str,
        ticks: list[Tick],
        interval_hours: float = 1.0,
        initial_balance: float = 5_000.0,
        entry_z: float = 2.5,
        exit_z: float = 0.3,
        stop_loss_z: float = 4.0,
        max_hold_hours: float = 168.0,
        max_position_pct: float = 0.20,
        max_open_positions: int = 2,
        cooldown_ticks: int = 5,
        z_window: int = 60,
        val_window: int = 300,
        revalidation_interval: int = 500,
    ) -> None:
        self.symbol_a = symbol_a
        self.symbol_b = symbol_b
        self.ticks = ticks
        self.interval_hours = interval_hours
        self.balance = initial_balance
        self.initial_balance = initial_balance
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.stop_loss_z = stop_loss_z
        self.max_hold_hours = max_hold_hours
        self.max_position_pct = max_position_pct
        self.max_open_positions = max_open_positions
        self.cooldown_ticks = cooldown_ticks

        self.state = BacktestState(
            z_window=z_window,
            val_window=val_window,
            revalidation_interval=revalidation_interval,
        )

        self.trades: list[BacktestTrade] = []
        self.equity_curve: list[tuple[str, float]] = []

        self._prices_a: list[float] = []
        self._prices_b: list[float] = []
        self._tick_index = 0
        self._cooldown_until_tick = 0

    def run(self) -> None:
        logger.info(
            "Running backtest: %s/%s  %d ticks  balance=%.0f",
            self.symbol_a, self.symbol_b, len(self.ticks), self.balance,
        )

        for i, tick in enumerate(self.ticks):
            self._tick_index = i
            self._prices_a.append(tick.price_a)
            self._prices_b.append(tick.price_b)
            self.state.push(tick.price_a, tick.price_b)

            # Periodic re-validation
            self.state.maybe_revalidate(
                self._prices_a, self._prices_b, self.interval_hours
            )

            # First-pass validation (simple version without statsmodels gate
            # for the first val_window ticks — allow basic trading once we
            # have enough data)
            if not self.state.is_valid and len(self._prices_a) >= self.state.val_window:
                self._bootstrap_validation()

            z = self.state.z_score()
            if z is None:
                self.equity_curve.append((tick.timestamp, self.balance))
                continue

            # Check open position for exit conditions
            if self.state.position is not None:
                self._check_exit(tick, z, i)

            # Try to open a new position
            if (
                self.state.position is None
                and self.state.is_valid
                and i >= self._cooldown_until_tick
            ):
                self._check_entry(tick, z, i)

            self.equity_curve.append((tick.timestamp, self.balance))

        # Force-close any open position at end of data
        if self.state.position is not None and self.ticks:
            last = self.ticks[-1]
            self._close_position(
                last, z or 0.0, len(self.ticks) - 1, "end_of_data"
            )

    def _bootstrap_validation(self) -> None:
        """Set is_valid=True with simple defaults if statsmodels unavailable."""
        try:
            from statsmodels.tsa.stattools import coint
        except ImportError:
            self.state.is_valid = True
            return

        n = self.state.val_window
        log_a = np.log(np.array(self._prices_a[-n:]))
        log_b = np.log(np.array(self._prices_b[-n:]))
        spread = log_a - log_b  # β=1 bootstrap
        self.state.hedge_ratio = 1.0
        self.state.spread_mean = float(np.mean(spread))
        self.state.spread_std = float(np.std(spread))
        _, p, _ = coint(log_a, log_b)
        self.state.is_valid = (p < 0.05)
        self.state.ticks_since_validation = 0

    def _check_entry(self, tick: Tick, z: float, tick_idx: int) -> None:
        if abs(z) < self.entry_z:
            return

        direction = "long_a" if z < 0 else "long_b"
        notional = (
            self.balance * self.max_position_pct / max(self.max_open_positions, 1)
        )
        beta = self.state.hedge_ratio
        qty_a = notional / tick.price_a
        qty_b = (notional * beta) / tick.price_b

        self.state.position = BacktestPosition(
            direction=direction,
            entry_ts=tick.timestamp,
            entry_price_a=tick.price_a,
            entry_price_b=tick.price_b,
            entry_z=z,
            hedge_ratio=beta,
            notional_usd=notional,
            qty_a=qty_a,
            qty_b=qty_b,
            entry_time_index=tick_idx,
        )

    def _check_exit(self, tick: Tick, z: float, tick_idx: int) -> None:
        pos = self.state.position
        hold_ticks = tick_idx - pos.entry_time_index
        hold_hours = hold_ticks * self.interval_hours

        if not self.state.is_valid:
            self._close_position(tick, z, tick_idx, "revalidation_fail")
        elif abs(z) <= self.exit_z:
            self._close_position(tick, z, tick_idx, "exit_signal")
        elif abs(z) >= self.stop_loss_z:
            self._close_position(tick, z, tick_idx, "stop_loss")
        elif hold_hours >= self.max_hold_hours:
            self._close_position(tick, z, tick_idx, "max_hold")

    def _close_position(
        self,
        tick: Tick,
        z: float,
        tick_idx: int,
        reason: str,
    ) -> None:
        pos = self.state.position
        if pos is None:
            return

        hold_ticks = tick_idx - pos.entry_time_index
        hold_hours = hold_ticks * self.interval_hours

        # Gross P&L: long leg gains, short leg loses
        if pos.direction == "long_a":
            pnl_a = pos.qty_a * (tick.price_a - pos.entry_price_a)
            pnl_b = -pos.qty_b * (tick.price_b - pos.entry_price_b)
        else:
            pnl_a = -pos.qty_a * (tick.price_a - pos.entry_price_a)
            pnl_b = pos.qty_b * (tick.price_b - pos.entry_price_b)

        gross = pnl_a + pnl_b
        cost = round_trip_cost_pct(hold_hours) * pos.notional_usd
        net = gross - cost

        self.balance += net

        trade = BacktestTrade(
            pair=f"{self.symbol_a}/{self.symbol_b}",
            direction=pos.direction,
            entry_ts=pos.entry_ts,
            exit_ts=tick.timestamp,
            entry_price_a=pos.entry_price_a,
            entry_price_b=pos.entry_price_b,
            exit_price_a=tick.price_a,
            exit_price_b=tick.price_b,
            hedge_ratio=pos.hedge_ratio,
            notional_usd=pos.notional_usd,
            qty_a=pos.qty_a,
            qty_b=pos.qty_b,
            entry_z=pos.entry_z,
            exit_z=z,
            gross_pnl=round(gross, 4),
            cost_usd=round(cost, 4),
            net_pnl=round(net, 4),
            hold_hours=round(hold_hours, 2),
            close_reason=reason,
        )
        self.trades.append(trade)
        self.state.position = None
        self._cooldown_until_tick = tick_idx + self.cooldown_ticks


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def summary(engine: BacktestEngine) -> dict:
    trades = engine.trades
    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "total_net_pnl": 0.0,
            "final_balance": engine.balance,
            "return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe": 0.0,
        }

    net_pnls = [t.net_pnl for t in trades]
    wins = sum(1 for p in net_pnls if p > 0)

    # Max drawdown from equity curve
    equity = [e for _, e in engine.equity_curve]
    peak = equity[0]
    max_dd = 0.0
    for e in equity:
        peak = max(peak, e)
        dd = (peak - e) / peak
        max_dd = max(max_dd, dd)

    # Rough Sharpe (daily returns from equity curve, annualised)
    equity_arr = np.array(equity)
    returns = np.diff(equity_arr) / equity_arr[:-1]
    sharpe = 0.0
    if len(returns) > 1 and returns.std() > 0:
        # Assume ticks are hourly; ~8760 hours per year
        ann_factor = np.sqrt(8760 / engine.interval_hours)
        sharpe = float(returns.mean() / returns.std() * ann_factor)

    return {
        "total_trades": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "win_rate": round(wins / len(trades) * 100, 1),
        "total_gross_pnl": round(sum(t.gross_pnl for t in trades), 2),
        "total_cost_usd": round(sum(t.cost_usd for t in trades), 2),
        "total_net_pnl": round(sum(net_pnls), 2),
        "avg_net_pnl": round(float(np.mean(net_pnls)), 2),
        "best_trade": round(max(net_pnls), 2),
        "worst_trade": round(min(net_pnls), 2),
        "initial_balance": engine.initial_balance,
        "final_balance": round(engine.balance, 2),
        "return_pct": round(
            (engine.balance - engine.initial_balance) / engine.initial_balance * 100, 2
        ),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe": round(sharpe, 3),
        "avg_hold_hours": round(float(np.mean([t.hold_hours for t in trades])), 2),
    }


def save_results(engine: BacktestEngine, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    pair_slug = f"{engine.symbol_a}_{engine.symbol_b}"

    # Trades CSV
    trades_path = out_dir / f"{pair_slug}_trades.csv"
    if engine.trades:
        fields = list(engine.trades[0].__dataclass_fields__.keys())
        with trades_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for t in engine.trades:
                writer.writerow({k: getattr(t, k) for k in fields})
        logger.info("Trades saved → %s", trades_path)

    # Equity curve CSV
    equity_path = out_dir / f"{pair_slug}_equity.csv"
    with equity_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "balance"])
        writer.writerows(engine.equity_curve)
    logger.info("Equity curve saved → %s", equity_path)


def print_summary(stats: dict) -> None:
    print(f"\n{'='*55}")
    print("  BACKTEST RESULTS")
    print(f"{'='*55}")
    print(f"  Trades          : {stats['total_trades']}  "
          f"(W:{stats.get('wins',0)}  L:{stats.get('losses',0)})")
    print(f"  Win rate        : {stats['win_rate']} %")
    print(f"  Total net P&L   : ${stats['total_net_pnl']:+.2f}")
    print(f"  Avg net P&L     : ${stats['avg_net_pnl']:+.2f}  per trade")
    print(f"  Best / Worst    : ${stats['best_trade']:+.2f} / ${stats['worst_trade']:+.2f}")
    print(f"  Gross P&L       : ${stats['total_gross_pnl']:+.2f}")
    print(f"  Total costs     : ${stats['total_cost_usd']:.2f}")
    print(f"  Balance         : ${stats['initial_balance']:.0f} → ${stats['final_balance']:.2f}")
    print(f"  Return          : {stats['return_pct']:+.2f} %")
    print(f"  Max drawdown    : {stats['max_drawdown_pct']:.2f} %")
    print(f"  Sharpe (ann.)   : {stats['sharpe']:.3f}")
    print(f"  Avg hold        : {stats['avg_hold_hours']:.1f} h")
    print(f"{'='*55}\n")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_ticks(path_a: Path, path_b: Path) -> list[Tick]:
    """Align two CSV files by timestamp and return merged ticks."""
    def _read(p: Path) -> dict[str, float]:
        data: dict[str, float] = {}
        with p.open() as f:
            for row in csv.DictReader(f):
                data[row["timestamp"]] = float(row["close"])
        return data

    prices_a = _read(path_a)
    prices_b = _read(path_b)
    common = sorted(set(prices_a) & set(prices_b))
    logger.info("Aligned %d common timestamps", len(common))
    return [Tick(ts, prices_a[ts], prices_b[ts]) for ts in common]


def _interval_hours_from_path(p: Path) -> float:
    mapping = {
        "1m": 1 / 60, "5m": 5 / 60, "15m": 0.25, "30m": 0.5,
        "1h": 1.0, "4h": 4.0, "1d": 24.0,
    }
    stem = p.stem  # e.g. ETHUSDT_1h
    parts = stem.split("_")
    return mapping.get(parts[-1] if len(parts) > 1 else "1h", 1.0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pairs trading backtest engine")
    parser.add_argument("--a", required=True, help="CSV file for symbol A")
    parser.add_argument("--b", required=True, help="CSV file for symbol B")
    parser.add_argument("--initial-balance", type=float, default=5_000.0)
    parser.add_argument("--entry-z", type=float, default=2.5)
    parser.add_argument("--exit-z", type=float, default=0.3)
    parser.add_argument("--stop-loss-z", type=float, default=4.0)
    parser.add_argument("--max-hold-hours", type=float, default=168.0)
    parser.add_argument("--z-window", type=int, default=60)
    parser.add_argument("--val-window", type=int, default=300)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("backend/backtest/results"),
        help="Directory to write CSV results",
    )
    args = parser.parse_args()

    path_a = Path(args.a)
    path_b = Path(args.b)
    ticks = load_ticks(path_a, path_b)
    interval_h = _interval_hours_from_path(path_a)

    sym_a = path_a.stem.split("_")[0]
    sym_b = path_b.stem.split("_")[0]

    engine = BacktestEngine(
        symbol_a=sym_a,
        symbol_b=sym_b,
        ticks=ticks,
        interval_hours=interval_h,
        initial_balance=args.initial_balance,
        entry_z=args.entry_z,
        exit_z=args.exit_z,
        stop_loss_z=args.stop_loss_z,
        max_hold_hours=args.max_hold_hours,
        z_window=args.z_window,
        val_window=args.val_window,
    )
    engine.run()

    stats = summary(engine)
    print_summary(stats)
    save_results(engine, args.out)
