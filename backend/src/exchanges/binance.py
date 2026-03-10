"""Binance WebSocket client for real-time BBO (Best Bid/Offer) data.

Uses the combined stream endpoint to subscribe to multiple bookTicker streams
in a single connection. Binance sends individual ticker updates per symbol.

Docs: https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams
"""

from __future__ import annotations

from ..models.ticker import Exchange, Ticker
from ..models.symbol_map import SymbolMapping
from .base import BaseExchangeWS, TickerCallback


class BinanceWS(BaseExchangeWS):
    """Binance combined stream for bookTicker (best bid/ask)."""

    def __init__(
        self,
        symbol_map: SymbolMapping,
        on_ticker: TickerCallback,
    ) -> None:
        super().__init__(symbol_map, on_ticker)
        native_symbols = symbol_map.get_native_symbols(Exchange.BINANCE)
        # Binance combined stream URL: /stream?streams=<s1>/<s2>/...
        streams = "/".join(f"{s.lower()}@bookTicker" for s in native_symbols)
        self._url = f"wss://stream.binance.com:9443/stream?streams={streams}"

    @property
    def exchange(self) -> Exchange:
        return Exchange.BINANCE

    @property
    def ws_url(self) -> str:
        return self._url

    def _build_subscribe_message(self) -> dict | list[dict]:
        # Subscriptions are embedded in the URL for combined streams,
        # so no explicit subscribe message needed. Return a no-op.
        # But we can also subscribe via message — we use URL approach.
        return []

    def _parse_message(self, raw: dict) -> Ticker | None:
        # Combined stream wraps in {"stream": "...", "data": {...}}
        data = raw.get("data", raw)

        if "b" not in data or "a" not in data:
            return None

        native_symbol = data.get("s", "").lower()
        unified = self.symbol_map.get_unified(Exchange.BINANCE, native_symbol)
        if not unified:
            return None

        return Ticker(
            exchange=Exchange.BINANCE,
            symbol=unified,
            best_bid=float(data["b"]),
            best_ask=float(data["a"]),
            bid_qty=float(data["B"]),
            ask_qty=float(data["A"]),
        )
