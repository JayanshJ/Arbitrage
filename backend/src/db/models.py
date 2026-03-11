"""SQLAlchemy models for paper trading.

Tables:
    - paper_trades: Each row is a completed arb trade (buy leg + sell leg).
    - balances: Tracks the virtual USD balance over time with snapshots.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Enum as SAEnum,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class TradeStatus(str, enum.Enum):
    EXECUTED = "executed"
    MISSED = "missed"  # opportunity expired before execution


class PaperTrade(Base):
    """A simulated arbitrage trade.

    Each row represents a full round trip: buy on one exchange, sell on another.
    """

    __tablename__ = "paper_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # What was traded
    symbol = Column(String(20), nullable=False, index=True)
    quantity = Column(Float, nullable=False)

    # Buy leg
    buy_exchange = Column(String(20), nullable=False)
    buy_price = Column(Float, nullable=False)
    buy_cost = Column(Float, nullable=False)  # qty * price
    buy_fee = Column(Float, nullable=False)   # in USD

    # Sell leg
    sell_exchange = Column(String(20), nullable=False)
    sell_price = Column(Float, nullable=False)
    sell_revenue = Column(Float, nullable=False)  # qty * price
    sell_fee = Column(Float, nullable=False)       # in USD

    # Slippage applied (in USD)
    slippage_cost = Column(Float, nullable=False, default=0.0)

    # Results
    gross_profit = Column(Float, nullable=False)  # sell_revenue - buy_cost
    net_profit = Column(Float, nullable=False)     # gross - fees - slippage
    net_profit_pct = Column(Float, nullable=False)

    # Balance after this trade
    balance_after = Column(Float, nullable=False)

    # Metadata
    status = Column(SAEnum(TradeStatus), nullable=False, default=TradeStatus.EXECUTED)
    notes = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<PaperTrade #{self.id} {self.symbol} "
            f"buy@{self.buy_exchange} sell@{self.sell_exchange} "
            f"net=${self.net_profit:+.2f}>"
        )


class PairsTrade(Base):
    """A simulated statistical arbitrage (pairs) trade.

    Each row is a complete round-trip: entry when |z-score| > 2σ,
    exit when |z-score| < 0.5σ (mean reversion).  Exit columns are
    NULL until the position is closed.
    """

    __tablename__ = "pairs_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Pair identity
    pair_id = Column(String(50), nullable=False, index=True)  # "ETH-USD:SOL-USD"
    symbol_a = Column(String(20), nullable=False)
    symbol_b = Column(String(20), nullable=False)

    # Direction: "long_a_short_b" or "short_a_long_b"
    direction = Column(String(30), nullable=False)

    # --- Entry leg ---
    entry_z_score = Column(Float, nullable=False)
    entry_price_a = Column(Float, nullable=False)
    entry_price_b = Column(Float, nullable=False)
    entry_time = Column(DateTime(timezone=True), nullable=False)
    notional_usd = Column(Float, nullable=False)      # USD allocated to leg A

    # Validated pair stats at entry time
    hedge_ratio = Column(Float, nullable=True)        # OLS β: spread = A − β·B
    half_life_hours = Column(Float, nullable=True)    # OU mean-reversion speed
    qty_a = Column(Float, nullable=True)              # units of A entered
    qty_b = Column(Float, nullable=True)              # units of B entered

    # --- Exit leg (NULL until closed) ---
    exit_z_score = Column(Float, nullable=True)
    exit_price_a = Column(Float, nullable=True)
    exit_price_b = Column(Float, nullable=True)
    exit_time = Column(DateTime(timezone=True), nullable=True)

    # --- P&L (real costs deducted) ---
    pnl_a = Column(Float, nullable=True)              # USD gross on leg A
    pnl_b = Column(Float, nullable=True)              # USD gross on leg B
    net_pnl = Column(Float, nullable=True)            # after fees+funding+slippage
    hold_seconds = Column(Float, nullable=True)
    pairs_balance_after = Column(Float, nullable=True)

    # "exit_signal" | "stop_loss" | "max_hold" | "revalidation_fail"
    close_reason = Column(String(30), nullable=True)

    # "open" | "closed"
    status = Column(String(20), nullable=False, default="open")

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<PairsTrade #{self.id} {self.pair_id} "
            f"{self.direction} z={self.entry_z_score:+.2f} "
            f"{'net=$' + f'{self.net_pnl:+.2f}' if self.net_pnl is not None else 'open'}>"
        )


class Balance(Base):
    """Periodic snapshot of the virtual trading balance.

    Recorded after each trade and on a periodic timer for PnL charting.
    """

    __tablename__ = "balances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    balance = Column(Float, nullable=False)
    trade_id = Column(Integer, nullable=True)  # NULL for periodic snapshots
    reason = Column(String(50), nullable=False)  # "trade", "initial", "snapshot"
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<Balance ${self.balance:,.2f} ({self.reason})>"
