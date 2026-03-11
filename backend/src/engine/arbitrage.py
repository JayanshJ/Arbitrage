"""Arbitrage detection engine.

Continuously scans all exchange pairs for a given symbol and calculates
the net profit after accounting for taker fees on both legs and estimated
slippage.

Arbitrage formula:
    Buy on Exchange A at ask_A, sell on Exchange B at bid_B.
    Gross profit %  = (bid_B - ask_A) / ask_A * 100
    Net profit %    = gross - fee_A_taker - fee_B_taker - slippage
    Opportunity exists when net profit > min_profit_pct.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Optional

from ..models.ticker import Exchange, Ticker
from ..cache.redis_cache import RedisCache

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "exchanges.json"


@dataclass
class ExchangeFees:
    maker_fee_pct: float
    taker_fee_pct: float


@dataclass
class ArbitrageOpportunity:
    """A detected arbitrage opportunity between two exchanges."""

    symbol: str
    buy_exchange: Exchange
    sell_exchange: Exchange
    buy_price: float  # ask on buy exchange
    sell_price: float  # bid on sell exchange
    buy_qty: float  # available qty at ask
    sell_qty: float  # available qty at bid
    gross_profit_pct: float
    total_fees_pct: float
    slippage_pct: float
    net_profit_pct: float
    timestamp: float = field(default_factory=time.time)

    @property
    def max_tradeable_qty(self) -> float:
        """The max quantity executable on both sides."""
        return min(self.buy_qty, self.sell_qty)

    @property
    def estimated_profit_usd(self) -> float:
        """Estimated USD profit for the max tradeable quantity."""
        spread = self.sell_price - self.buy_price
        gross = spread * self.max_tradeable_qty
        fees = (
            self.buy_price * self.max_tradeable_qty * self.total_fees_pct / 100
        )
        return gross - fees

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "buy_exchange": self.buy_exchange.value,
            "sell_exchange": self.sell_exchange.value,
            "buy_price": self.buy_price,
            "sell_price": self.sell_price,
            "buy_qty": self.buy_qty,
            "sell_qty": self.sell_qty,
            "max_tradeable_qty": self.max_tradeable_qty,
            "gross_profit_pct": round(self.gross_profit_pct, 4),
            "total_fees_pct": round(self.total_fees_pct, 4),
            "slippage_pct": round(self.slippage_pct, 4),
            "net_profit_pct": round(self.net_profit_pct, 4),
            "estimated_profit_usd": round(self.estimated_profit_usd, 4),
            "timestamp": self.timestamp,
        }


class ArbitrageEngine:
    """Scans exchange pairs and detects profitable arbitrage opportunities."""

    def __init__(
        self,
        cache: RedisCache,
        fees: dict[Exchange, ExchangeFees],
        slippage_pct: float = 0.10,
        min_profit_pct: float = 0.10,
    ) -> None:
        self.cache = cache
        self.fees = fees
        self.slippage_pct = slippage_pct
        self.min_profit_pct = min_profit_pct

    @classmethod
    def from_config(
        cls, cache: RedisCache, config_path: Path = CONFIG_PATH
    ) -> ArbitrageEngine:
        with open(config_path) as f:
            data = json.load(f)

        fees = {}
        for ex in Exchange:
            ex_config = data["exchanges"].get(ex.value, {})
            fees[ex] = ExchangeFees(
                maker_fee_pct=ex_config.get("maker_fee_pct", 0.10),
                taker_fee_pct=ex_config.get("taker_fee_pct", 0.10),
            )

        return cls(
            cache=cache,
            fees=fees,
            slippage_pct=data.get("slippage_pct", 0.10),
            min_profit_pct=data.get("min_profit_pct", 0.10),
        )

    def evaluate_pair(
        self,
        symbol: str,
        ticker_a: Ticker,
        ticker_b: Ticker,
    ) -> Optional[ArbitrageOpportunity]:
        """Check if buying on A and selling on B is profitable.

        Buy at A's ask, sell at B's bid.
        """
        ask_a = ticker_a.best_ask
        bid_b = ticker_b.best_bid

        if ask_a <= 0 or bid_b <= 0:
            return None

        gross_pct = (bid_b - ask_a) / ask_a * 100

        fee_buy = self.fees[ticker_a.exchange].taker_fee_pct
        fee_sell = self.fees[ticker_b.exchange].taker_fee_pct
        total_fees = fee_buy + fee_sell

        net_pct = gross_pct - total_fees - self.slippage_pct

        if net_pct <= 0:
            return None

        return ArbitrageOpportunity(
            symbol=symbol,
            buy_exchange=ticker_a.exchange,
            sell_exchange=ticker_b.exchange,
            buy_price=ask_a,
            sell_price=bid_b,
            buy_qty=ticker_a.ask_qty,
            sell_qty=ticker_b.bid_qty,
            gross_profit_pct=gross_pct,
            total_fees_pct=total_fees,
            slippage_pct=self.slippage_pct,
            net_profit_pct=net_pct,
        )

    async def scan_symbol(
        self, symbol: str
    ) -> list[ArbitrageOpportunity]:
        """Scan all exchange pairs for arbitrage on a given symbol."""
        tickers = await self.cache.get_all_tickers_for_symbol(symbol)

        if len(tickers) < 2:
            return []

        opportunities = []
        exchanges = list(tickers.keys())

        for ex_a, ex_b in combinations(exchanges, 2):
            # Check both directions: buy A sell B, and buy B sell A
            opp = self.evaluate_pair(symbol, tickers[ex_a], tickers[ex_b])
            if opp and opp.net_profit_pct >= self.min_profit_pct:
                opportunities.append(opp)

            opp_rev = self.evaluate_pair(symbol, tickers[ex_b], tickers[ex_a])
            if opp_rev and opp_rev.net_profit_pct >= self.min_profit_pct:
                opportunities.append(opp_rev)

        # Sort by net profit descending
        opportunities.sort(key=lambda o: o.net_profit_pct, reverse=True)

        # Publish to Redis pub/sub for downstream consumers
        for opp in opportunities:
            await self.cache.publish_opportunity(opp.to_dict())

        return opportunities

    async def scan_all(
        self, symbols: list[str]
    ) -> list[ArbitrageOpportunity]:
        """Scan all symbols for arbitrage opportunities."""
        all_opps = []
        for symbol in symbols:
            opps = await self.scan_symbol(symbol)
            all_opps.extend(opps)
        return all_opps
