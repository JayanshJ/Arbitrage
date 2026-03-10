"""Standardized ticker model for cross-exchange price data."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class Exchange(str, Enum):
    BINANCE = "binance"
    KRAKEN = "kraken"
    COINBASE = "coinbase"


@dataclass(frozen=True)
class Ticker:
    """Unified ticker representing best bid/ask from any exchange.

    Attributes:
        exchange: Source exchange identifier.
        symbol: Unified internal symbol (e.g. "BTC-USD").
        best_bid: Highest bid price.
        best_ask: Lowest ask price.
        bid_qty: Quantity available at best bid.
        ask_qty: Quantity available at best ask.
        exchange_ts: Timestamp from the exchange (epoch seconds), if provided.
        local_ts: Local receipt timestamp (epoch seconds). Always set on creation.
    """

    exchange: Exchange
    symbol: str
    best_bid: float
    best_ask: float
    bid_qty: float
    ask_qty: float
    exchange_ts: float | None = None
    local_ts: float = field(default_factory=time.time)

    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread_bps(self) -> float:
        """Spread in basis points."""
        if self.mid_price == 0:
            return 0.0
        return ((self.best_ask - self.best_bid) / self.mid_price) * 10_000

    def __str__(self) -> str:
        return (
            f"[{self.exchange.value}] {self.symbol} "
            f"bid={self.best_bid:.2f} ({self.bid_qty:.6f}) "
            f"ask={self.best_ask:.2f} ({self.ask_qty:.6f}) "
            f"spread={self.spread_bps:.1f}bps"
        )
