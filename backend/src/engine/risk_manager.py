"""Hard risk limits for the pairs trading engine.

These limits are non-negotiable — they override every other signal.
They exist to prevent catastrophic losses from model failure, regime
changes, bugs, or extreme market conditions.

Rules enforced:
  - Max drawdown  : halt ALL trading if equity drops > 15 % from peak
  - Position size : never allocate more than 20 % of balance per trade
  - Max positions : at most 2 concurrent open pairs
  - Max hold      : force-close any position held > 7 days
  - Stop-loss     : close immediately if |z| > 4 σ (pair diverging)
  - Stale price   : reject any ticker older than 2 s
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class RiskManager:
    """Stateful guard that wraps the pairs engine decision logic."""

    def __init__(
        self,
        initial_capital: float,
        max_drawdown_pct: float = 0.15,    # halt at −15 %
        max_position_pct: float = 0.20,    # max 20 % of balance per trade
        max_open_positions: int = 2,
        max_hold_days: float = 7.0,
        stop_loss_z: float = 4.0,          # close if |z| diverges beyond 4 σ
    ) -> None:
        self.initial_capital = initial_capital
        self.peak_capital = initial_capital
        self.max_drawdown_pct = max_drawdown_pct
        self.max_position_pct = max_position_pct
        self.max_open_positions = max_open_positions
        self.max_hold_days = max_hold_days
        self.stop_loss_z = stop_loss_z

        self.halted: bool = False
        self.halt_reason: Optional[str] = None

        self._alerts = None  # injected after construction

    def set_alerts(self, alerts) -> None:
        self._alerts = alerts

    # ------------------------------------------------------------------ #
    # Pre-trade checks                                                     #
    # ------------------------------------------------------------------ #

    def can_open_trade(
        self,
        current_capital: float,
        open_positions: int,
    ) -> tuple[bool, str]:
        """Return (allowed, reason).  Call before opening any position."""
        if self.halted:
            return False, f"System halted: {self.halt_reason}"

        if not self._update_drawdown(current_capital):
            return False, "Max drawdown reached"

        if open_positions >= self.max_open_positions:
            return (
                False,
                f"Max open positions ({self.max_open_positions}) already active",
            )

        return True, ""

    def position_size_usd(self, current_capital: float) -> float:
        """Max notional per leg, derived from risk limits."""
        return (
            current_capital
            * self.max_position_pct
            / max(self.max_open_positions, 1)
        )

    # ------------------------------------------------------------------ #
    # In-trade checks (called on every tick while position is open)       #
    # ------------------------------------------------------------------ #

    def check_stop_loss(
        self,
        current_z: float,
        direction: str,
        entry_z: float,  # kept for future momentum-based stops
    ) -> tuple[bool, str]:
        """Return (should_close, reason).

        Triggers when |z| extends to stop_loss_z — the pair is diverging
        rather than reverting, so we cut the loss immediately.
        """
        if abs(current_z) >= self.stop_loss_z:
            return (
                True,
                f"stop_loss: |z|={abs(current_z):.2f} ≥ {self.stop_loss_z:.1f}",
            )
        return False, ""

    def check_max_hold(self, entry_time: float) -> tuple[bool, str]:
        """Force-close if the position has been held too long."""
        hold_days = (time.time() - entry_time) / 86_400
        if hold_days >= self.max_hold_days:
            return True, f"max_hold: {hold_days:.1f} d ≥ {self.max_hold_days} d"
        return False, ""

    def check_stale_price(self, ticker_age_seconds: float) -> bool:
        """Return False if the price is too old to be trusted."""
        return ticker_age_seconds < 2.0

    # ------------------------------------------------------------------ #
    # Capital tracking                                                     #
    # ------------------------------------------------------------------ #

    def update_capital(self, current_capital: float) -> None:
        """Call after every trade to keep peak-capital accurate."""
        self._update_drawdown(current_capital)

    def _update_drawdown(self, current_capital: float) -> bool:
        """Update peak and return False (halt) if drawdown exceeded."""
        self.peak_capital = max(self.peak_capital, current_capital)
        drawdown = (self.peak_capital - current_capital) / self.peak_capital
        if drawdown >= self.max_drawdown_pct:
            reason = (
                f"Drawdown {drawdown:.1%} exceeded "
                f"{self.max_drawdown_pct:.0%} limit"
            )
            self._halt(reason)
            return False
        return True

    def _halt(self, reason: str) -> None:
        if self.halted:
            return
        self.halted = True
        self.halt_reason = reason
        logger.critical("⛔ RISK HALT: %s", reason)
        if self._alerts:
            try:
                asyncio.create_task(self._alerts.on_halt(reason))
            except RuntimeError:
                pass  # no event loop yet at startup

    def reset_halt(self) -> None:
        """Manual override — use only after investigating the halt reason."""
        logger.warning("Risk halt cleared manually.")
        self.halted = False
        self.halt_reason = None

    def status_dict(self) -> dict:
        return {
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "peak_capital": round(self.peak_capital, 2),
            "max_drawdown_pct": self.max_drawdown_pct,
            "max_position_pct": self.max_position_pct,
            "max_open_positions": self.max_open_positions,
            "max_hold_days": self.max_hold_days,
            "stop_loss_z": self.stop_loss_z,
        }
