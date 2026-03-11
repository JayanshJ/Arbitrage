"""Real cost model for production pairs trading.

Accounts for every dollar that leaves your account on each trade:
  - Taker fees on open and close (both legs)
  - Estimated slippage from market impact
  - Funding rate on the futures leg (Binance USDT-perpetuals, every 8 h)

Usage:
    model = RealCostModel()
    breakeven = model.min_gross_profit_pct(hold_hours=12)
    net = model.net_pnl(gross_pnl_usd=8.50, notional_usd=500, hold_hours=12)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExchangeFees:
    taker_fee_pct: float   # e.g. 0.0005 for 0.05 %
    maker_fee_pct: float


# ── Current fee schedules (March 2026) ─────────────────────────────────── #
BINANCE_FUTURES = ExchangeFees(taker_fee_pct=0.0005, maker_fee_pct=0.0002)
KRAKEN_FUTURES  = ExchangeFees(taker_fee_pct=0.0005, maker_fee_pct=0.0002)

# Funding rate charged every 8 h on Binance USDT-perpetuals.
# ~0.01 %/8 h in neutral market; can spike to 0.1 %/8 h in strong bull runs.
FUNDING_RATE_8H: float = 0.0001   # 0.01 % per 8 h ≈ 0.9 % per month

# Market impact / slippage per leg (retail-sized orders on deep markets).
SLIPPAGE_PER_LEG: float = 0.0002  # 0.02 %


class RealCostModel:
    """Calculate the true all-in cost of a round-trip pairs trade."""

    def __init__(
        self,
        leg_a_fees: ExchangeFees = BINANCE_FUTURES,
        leg_b_fees: ExchangeFees = BINANCE_FUTURES,
        funding_rate_8h: float = FUNDING_RATE_8H,
        slippage_per_leg: float = SLIPPAGE_PER_LEG,
    ) -> None:
        self.leg_a = leg_a_fees
        self.leg_b = leg_b_fees
        self.funding_rate_8h = funding_rate_8h
        self.slippage = slippage_per_leg

    def round_trip_cost_pct(self, hold_hours: float) -> float:
        """Total cost as a fraction of notional for one complete pairs trade.

        Breakdown (per leg, per direction):
            open_fees  = taker_a + taker_b
            close_fees = taker_a + taker_b
            slippage   = 4 × slippage_per_leg  (open + close, both legs)
            funding    = (hold_hours / 8) × funding_rate_8h  (futures only)
        """
        open_fees  = self.leg_a.taker_fee_pct + self.leg_b.taker_fee_pct
        close_fees = open_fees
        slippage   = self.slippage * 4
        funding    = self.funding_rate_8h * (hold_hours / 8)
        return open_fees + close_fees + slippage + funding

    def min_gross_profit_pct(self, hold_hours: float) -> float:
        """Minimum gross P&L fraction needed just to break even."""
        return self.round_trip_cost_pct(hold_hours)

    def net_pnl(
        self,
        gross_pnl_usd: float,
        notional_usd: float,
        hold_hours: float,
    ) -> float:
        """USD net profit after deducting all costs."""
        total_cost = notional_usd * self.round_trip_cost_pct(hold_hours)
        return gross_pnl_usd - total_cost

    def cost_usd(self, notional_usd: float, hold_hours: float) -> float:
        """Absolute cost in USD for a given notional and hold time."""
        return notional_usd * self.round_trip_cost_pct(hold_hours)
