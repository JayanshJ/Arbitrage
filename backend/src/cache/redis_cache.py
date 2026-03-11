"""Redis cache layer for storing and retrieving ticker data.

Stores each ticker as a Redis hash at key `ticker:{exchange}:{symbol}` with
a 30-second TTL. Stale data auto-expires so the engine never acts on
prices from a disconnected exchange.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

import redis.asyncio as redis

from ..models.ticker import Exchange, Ticker

logger = logging.getLogger(__name__)

TICKER_KEY_PREFIX = "ticker"
TICKER_TTL_SECONDS = 30


def _ticker_key(exchange: Exchange, symbol: str) -> str:
    return f"{TICKER_KEY_PREFIX}:{exchange.value}:{symbol}"


class RedisCache:
    """Async Redis wrapper for ticker storage and retrieval."""

    def __init__(self, redis_client: redis.Redis) -> None:
        self._r = redis_client

    @classmethod
    async def connect(
        cls,
        url: str = "redis://localhost:6379/0",
    ) -> RedisCache:
        client = redis.from_url(url, decode_responses=True)
        await client.ping()
        logger.info("Connected to Redis at %s", url)
        return cls(client)

    async def close(self) -> None:
        await self._r.aclose()

    async def set_ticker(self, ticker: Ticker) -> None:
        """Store a ticker in Redis with TTL."""
        key = _ticker_key(ticker.exchange, ticker.symbol)
        data = {
            "exchange": ticker.exchange.value,
            "symbol": ticker.symbol,
            "best_bid": str(ticker.best_bid),
            "best_ask": str(ticker.best_ask),
            "bid_qty": str(ticker.bid_qty),
            "ask_qty": str(ticker.ask_qty),
            "local_ts": str(ticker.local_ts),
        }
        pipe = self._r.pipeline()
        pipe.hset(key, mapping=data)
        pipe.expire(key, TICKER_TTL_SECONDS)
        await pipe.execute()

    async def get_ticker(
        self, exchange: Exchange, symbol: str
    ) -> Optional[Ticker]:
        """Retrieve a ticker from Redis. Returns None if expired/missing."""
        key = _ticker_key(exchange, symbol)
        data = await self._r.hgetall(key)
        if not data:
            return None

        return Ticker(
            exchange=Exchange(data["exchange"]),
            symbol=data["symbol"],
            best_bid=float(data["best_bid"]),
            best_ask=float(data["best_ask"]),
            bid_qty=float(data["bid_qty"]),
            ask_qty=float(data["ask_qty"]),
            local_ts=float(data["local_ts"]),
        )

    async def get_all_tickers_for_symbol(
        self, symbol: str
    ) -> dict[Exchange, Ticker]:
        """Get the latest ticker from each exchange for a given symbol."""
        result = {}
        for exchange in Exchange:
            ticker = await self.get_ticker(exchange, symbol)
            if ticker:
                result[exchange] = ticker
        return result

    async def publish_opportunity(self, opportunity: dict) -> None:
        """Publish an arbitrage opportunity to a Redis pub/sub channel.

        The frontend (Phase 4) will subscribe to this channel for
        real-time streaming.
        """
        await self._r.publish(
            "arbitrage:opportunities", json.dumps(opportunity)
        )
