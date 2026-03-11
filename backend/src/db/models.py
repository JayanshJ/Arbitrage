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
