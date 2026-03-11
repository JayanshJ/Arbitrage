"""Integration tests for Redis cache using fakeredis."""

import pytest
import fakeredis.aioredis

from src.models.ticker import Exchange, Ticker
from src.cache.redis_cache import RedisCache


@pytest.fixture
async def cache():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    c = RedisCache(client)
    yield c
    await c.close()


@pytest.mark.asyncio
async def test_set_and_get_ticker(cache):
    ticker = Ticker(
        exchange=Exchange.BINANCE,
        symbol="BTC-USD",
        best_bid=100_000.0,
        best_ask=100_001.0,
        bid_qty=1.5,
        ask_qty=2.0,
    )
    await cache.set_ticker(ticker)
    result = await cache.get_ticker(Exchange.BINANCE, "BTC-USD")

    assert result is not None
    assert result.exchange == Exchange.BINANCE
    assert result.symbol == "BTC-USD"
    assert result.best_bid == 100_000.0
    assert result.best_ask == 100_001.0
    assert result.bid_qty == 1.5
    assert result.ask_qty == 2.0


@pytest.mark.asyncio
async def test_get_missing_ticker(cache):
    result = await cache.get_ticker(Exchange.KRAKEN, "BTC-USD")
    assert result is None


@pytest.mark.asyncio
async def test_get_all_tickers_for_symbol(cache):
    for ex, bid, ask in [
        (Exchange.BINANCE, 100_000.0, 100_001.0),
        (Exchange.KRAKEN, 100_010.0, 100_011.0),
        (Exchange.COINBASE, 99_990.0, 99_991.0),
    ]:
        await cache.set_ticker(Ticker(
            exchange=ex, symbol="BTC-USD",
            best_bid=bid, best_ask=ask,
            bid_qty=1.0, ask_qty=1.0,
        ))

    tickers = await cache.get_all_tickers_for_symbol("BTC-USD")
    assert len(tickers) == 3
    assert Exchange.BINANCE in tickers
    assert Exchange.KRAKEN in tickers
    assert Exchange.COINBASE in tickers


@pytest.mark.asyncio
async def test_overwrite_ticker(cache):
    t1 = Ticker(
        exchange=Exchange.BINANCE, symbol="ETH-USD",
        best_bid=2000.0, best_ask=2001.0,
        bid_qty=1.0, ask_qty=1.0,
    )
    t2 = Ticker(
        exchange=Exchange.BINANCE, symbol="ETH-USD",
        best_bid=2050.0, best_ask=2051.0,
        bid_qty=2.0, ask_qty=2.0,
    )
    await cache.set_ticker(t1)
    await cache.set_ticker(t2)

    result = await cache.get_ticker(Exchange.BINANCE, "ETH-USD")
    assert result.best_bid == 2050.0
    assert result.bid_qty == 2.0
