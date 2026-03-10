"""Coinbase Advanced Trade WebSocket client for real-time BBO data.

Uses the public (unauthenticated) market data WebSocket feed.
The "ticker" channel provides best bid/ask with quantities.

Docs: https://docs.cdp.coinbase.com/advanced-trade/docs/ws-channels#ticker-channel
"""

from __future__ import annotations

from ..models.ticker import Exchange, Ticker
from ..models.symbol_map import SymbolMapping
from .base import BaseExchangeWS, TickerCallback

WS_URL = "wss://advanced-trade-ws.coinbase.com"


class CoinbaseWS(BaseExchangeWS):
    """Coinbase Advanced Trade ticker subscription."""

    def __init__(
        self,
        symbol_map: SymbolMapping,
        on_ticker: TickerCallback,
    ) -> None:
        super().__init__(symbol_map, on_ticker)
        self._native_symbols = symbol_map.get_native_symbols(Exchange.COINBASE)

    @property
    def exchange(self) -> Exchange:
        return Exchange.COINBASE

    @property
    def ws_url(self) -> str:
        return WS_URL

    def _build_subscribe_message(self) -> dict:
        return {
            "type": "subscribe",
            "product_ids": self._native_symbols,
            "channel": "ticker",
        }

    def _parse_message(self, raw: dict) -> Ticker | None:
        # Coinbase Advanced Trade sends:
        # {"channel": "ticker", "events": [{"type": "update", "tickers": [...]}]}
        if raw.get("channel") != "ticker":
            return None

        events = raw.get("events", [])
        for event in events:
            tickers = event.get("tickers", [])
            for t in tickers:
                native_symbol = t.get("product_id", "")
                unified = self.symbol_map.get_unified(
                    Exchange.COINBASE, native_symbol
                )
                if not unified:
                    continue

                best_bid = float(t.get("best_bid", 0))
                best_ask = float(t.get("best_ask", 0))
                bid_qty = float(t.get("best_bid_quantity", 0))
                ask_qty = float(t.get("best_ask_quantity", 0))

                if best_bid == 0 or best_ask == 0:
                    continue

                return Ticker(
                    exchange=Exchange.COINBASE,
                    symbol=unified,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    bid_qty=bid_qty,
                    ask_qty=ask_qty,
                )

        return None
