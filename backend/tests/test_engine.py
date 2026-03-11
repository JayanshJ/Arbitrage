"""Tests for the arbitrage detection engine."""

import pytest
from src.models.ticker import Exchange, Ticker
from src.engine.arbitrage import ArbitrageEngine, ExchangeFees, ArbitrageOpportunity


def make_ticker(exchange, symbol, bid, ask, bid_qty=1.0, ask_qty=1.0):
    return Ticker(
        exchange=exchange,
        symbol=symbol,
        best_bid=bid,
        best_ask=ask,
        bid_qty=bid_qty,
        ask_qty=ask_qty,
    )


def make_engine(cache=None, slippage=0.10, min_profit=0.10):
    fees = {
        Exchange.BINANCE: ExchangeFees(maker_fee_pct=0.10, taker_fee_pct=0.10),
        Exchange.KRAKEN: ExchangeFees(maker_fee_pct=0.16, taker_fee_pct=0.26),
        Exchange.COINBASE: ExchangeFees(maker_fee_pct=0.40, taker_fee_pct=0.60),
    }
    return ArbitrageEngine(
        cache=cache,
        fees=fees,
        slippage_pct=slippage,
        min_profit_pct=min_profit,
    )


def test_no_arbitrage_same_price():
    """Same price on both exchanges = no opportunity."""
    engine = make_engine()
    t_a = make_ticker(Exchange.BINANCE, "BTC-USD", 100_000.0, 100_001.0)
    t_b = make_ticker(Exchange.KRAKEN, "BTC-USD", 100_000.0, 100_001.0)
    opp = engine.evaluate_pair("BTC-USD", t_a, t_b)
    assert opp is None


def test_no_arbitrage_fees_eat_spread():
    """Small spread that disappears after fees + slippage."""
    engine = make_engine()
    # Buy on Binance at 100_000, sell on Coinbase at 100_050
    # Gross = 0.05%, fees = 0.10 + 0.60 = 0.70%, slippage = 0.10%
    # Net = 0.05 - 0.70 - 0.10 = -0.75% -> no opportunity
    t_a = make_ticker(Exchange.BINANCE, "BTC-USD", 99_999.0, 100_000.0)
    t_b = make_ticker(Exchange.COINBASE, "BTC-USD", 100_050.0, 100_051.0)
    opp = engine.evaluate_pair("BTC-USD", t_a, t_b)
    assert opp is None


def test_profitable_arbitrage():
    """Clear arbitrage: buy cheap on Binance, sell expensive on Kraken."""
    engine = make_engine(min_profit=0.01)
    # Buy on Binance at ask 100_000, sell on Kraken at bid 101_000
    # Gross = 1.0%, fees = 0.10 + 0.26 = 0.36%, slippage = 0.10%
    # Net = 1.0 - 0.36 - 0.10 = 0.54%
    t_a = make_ticker(Exchange.BINANCE, "BTC-USD", 99_999.0, 100_000.0, ask_qty=0.5)
    t_b = make_ticker(Exchange.KRAKEN, "BTC-USD", 101_000.0, 101_001.0, bid_qty=0.3)

    opp = engine.evaluate_pair("BTC-USD", t_a, t_b)
    assert opp is not None
    assert opp.buy_exchange == Exchange.BINANCE
    assert opp.sell_exchange == Exchange.KRAKEN
    assert abs(opp.gross_profit_pct - 1.0) < 0.01
    assert abs(opp.net_profit_pct - 0.54) < 0.01
    assert opp.max_tradeable_qty == 0.3  # min of ask_qty, bid_qty


def test_opportunity_to_dict():
    """Verify serialization for Redis pub/sub."""
    engine = make_engine(min_profit=0.01)
    t_a = make_ticker(Exchange.BINANCE, "ETH-USD", 1999.0, 2000.0, ask_qty=10.0)
    t_b = make_ticker(Exchange.KRAKEN, "ETH-USD", 2050.0, 2051.0, bid_qty=5.0)

    opp = engine.evaluate_pair("ETH-USD", t_a, t_b)
    assert opp is not None

    d = opp.to_dict()
    assert d["symbol"] == "ETH-USD"
    assert d["buy_exchange"] == "binance"
    assert d["sell_exchange"] == "kraken"
    assert d["net_profit_pct"] > 0
    assert d["max_tradeable_qty"] == 5.0
    assert "estimated_profit_usd" in d


def test_reverse_direction_checked():
    """Engine should detect opportunity in the reverse direction too."""
    engine = make_engine(min_profit=0.01)
    # Kraken is cheaper, Binance is more expensive
    t_kraken = make_ticker(Exchange.KRAKEN, "BTC-USD", 99_999.0, 100_000.0)
    t_binance = make_ticker(Exchange.BINANCE, "BTC-USD", 101_500.0, 101_501.0)

    # Buy Kraken (ask=100_000), sell Binance (bid=101_500) -> profitable
    opp = engine.evaluate_pair("BTC-USD", t_kraken, t_binance)
    assert opp is not None
    assert opp.buy_exchange == Exchange.KRAKEN
    assert opp.sell_exchange == Exchange.BINANCE
