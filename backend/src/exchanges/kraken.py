"""Kraken WebSocket v2 client for real-time BBO data.

Uses Kraken's WebSocket v2 API which sends ticker updates including
best bid/ask with quantities.

Docs: https://docs.kraken.com/api/docs/websocket-v2/ticker
"""

from __future__ import annotations

from ..models.ticker import Exchange, Ticker
from ..models.symbol_map import SymbolMapping
from .base import BaseExchangeWS, TickerCallback

WS_URL = "wss://ws.kraken.com/v2"


class KrakenWS(BaseExchangeWS):
    """Kraken WebSocket v2 ticker subscription."""

    def __init__(
        self,
        symbol_map: SymbolMapping,
        on_ticker: TickerCallback,
    ) -> None:
        super().__init__(symbol_map, on_ticker)
        self._native_symbols = symbol_map.get_native_symbols(Exchange.KRAKEN)

    @property
    def exchange(self) -> Exchange:
        return Exchange.KRAKEN

    @property
    def ws_url(self) -> str:
        return WS_URL

    def _build_subscribe_message(self) -> dict:
        return {
            "method": "subscribe",
            "params": {
                "channel": "ticker",
                "symbol": self._native_symbols,
            },
        }

    def _parse_message(self, raw: dict) -> Ticker | None:
        # Kraken v2 ticker update: {"channel": "ticker", "type": "update", "data": [...]}
        if raw.get("channel") != "ticker":
            return None

        data_list = raw.get("data")
        if not data_list:
            return None

        for data in data_list:
            native_symbol = data.get("symbol", "")
            unified = self.symbol_map.get_unified(Exchange.KRAKEN, native_symbol)
            if not unified:
                continue

            return Ticker(
                exchange=Exchange.KRAKEN,
                symbol=unified,
                best_bid=float(data["bid"]),
                best_ask=float(data["ask"]),
                bid_qty=float(data["bid_qty"]),
                ask_qty=float(data["ask_qty"]),
            )

        return None
