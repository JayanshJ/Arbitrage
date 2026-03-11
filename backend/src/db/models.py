"""SQLAlchemy ORM models — pairs trading only."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Float, Integer, String, func
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class PairsTrade(Base):
    """One complete round-trip for a statistical arbitrage (pairs) position.

    Entry columns are written when the position opens.
    Exit / P&L columns are NULL until the position closes.
    """

    __tablename__ = "pairs_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Pair identity
    pair_id   = Column(String(50), nullable=False, index=True)  # "ETH-USD:SOL-USD"
    symbol_a  = Column(String(20), nullable=False)
    symbol_b  = Column(String(20), nullable=False)

    # Direction: "long_a_short_b" | "short_a_long_b"
    direction = Column(String(30), nullable=False)

    # Entry
    entry_z_score  = Column(Float, nullable=False)
    entry_price_a  = Column(Float, nullable=False)
    entry_price_b  = Column(Float, nullable=False)
    entry_time     = Column(DateTime(timezone=True), nullable=False)
    notional_usd   = Column(Float, nullable=False)

    # Validated pair stats at entry
    hedge_ratio      = Column(Float, nullable=True)  # OLS beta: spread = A - beta*B
    half_life_hours  = Column(Float, nullable=True)  # OU mean-reversion speed (h)
    qty_a            = Column(Float, nullable=True)  # units of A
    qty_b            = Column(Float, nullable=True)  # units of B

    # Exit (NULL while open)
    exit_z_score  = Column(Float, nullable=True)
    exit_price_a  = Column(Float, nullable=True)
    exit_price_b  = Column(Float, nullable=True)
    exit_time     = Column(DateTime(timezone=True), nullable=True)

    # P&L (real costs deducted)
    pnl_a               = Column(Float, nullable=True)
    pnl_b               = Column(Float, nullable=True)
    net_pnl             = Column(Float, nullable=True)
    hold_seconds        = Column(Float, nullable=True)
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
        pnl = f"net=${self.net_pnl:+.2f}" if self.net_pnl is not None else "open"
        return f"<PairsTrade #{self.id} {self.pair_id} {self.direction} {pnl}>"
