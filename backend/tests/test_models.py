"""Tests for data models and symbol mapping."""

from src.models.ticker import Exchange, Ticker
from src.models.symbol_map import SymbolMapping


def test_ticker_spread_bps():
    t = Ticker(
        exchange=Exchange.BINANCE,
        symbol="BTC-USD",
        best_bid=100_000.0,
        best_ask=100_010.0,
        bid_qty=1.0,
        ask_qty=1.0,
    )
    # Spread = 10 / 100005 * 10000 ≈ 1.0 bps
    assert abs(t.spread_bps - 1.0) < 0.1


def test_ticker_mid_price():
    t = Ticker(
        exchange=Exchange.BINANCE,
        symbol="BTC-USD",
        best_bid=99_990.0,
        best_ask=100_010.0,
        bid_qty=0.5,
        ask_qty=0.5,
    )
    assert t.mid_price == 100_000.0


def test_symbol_map_from_config():
    sm = SymbolMapping.from_config()
    assert "BTC-USD" in sm.unified_symbols
    assert sm.get_unified(Exchange.BINANCE, "btcusdt") == "BTC-USD"
    assert sm.get_unified(Exchange.KRAKEN, "XBT/USD") == "BTC-USD"
    assert sm.get_unified(Exchange.COINBASE, "BTC-USD") == "BTC-USD"


def test_symbol_map_native_lookup():
    sm = SymbolMapping.from_config()
    assert sm.get_native(Exchange.BINANCE, "BTC-USD") == "btcusdt"
    assert sm.get_native(Exchange.KRAKEN, "BTC-USD") == "XBT/USD"


def test_symbol_map_case_insensitive():
    sm = SymbolMapping.from_config()
    assert sm.get_unified(Exchange.BINANCE, "BTCUSDT") == "BTC-USD"
    assert sm.get_unified(Exchange.BINANCE, "BtCuSdT") == "BTC-USD"
