"""Abstract base class for exchange WebSocket clients."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Awaitable

from ..models.ticker import Exchange, Ticker
from ..models.symbol_map import SymbolMapping

TickerCallback = Callable[[Ticker], Awaitable[None]]

logger = logging.getLogger(__name__)


class BaseExchangeWS(ABC):
    """Base class providing reconnection logic and a standard interface.

    Subclasses implement:
        - `exchange` property: which Exchange enum this client represents.
        - `ws_url` property: the WebSocket endpoint URL.
        - `_build_subscribe_message()`: the subscription payload.
        - `_parse_message(raw)`: parse a raw WS message into a Ticker or None.
    """

    INITIAL_BACKOFF = 1.0
    MAX_BACKOFF = 60.0
    BACKOFF_FACTOR = 2.0
    PING_INTERVAL = 20
    PING_TIMEOUT = 10

    def __init__(
        self,
        symbol_map: SymbolMapping,
        on_ticker: TickerCallback,
    ) -> None:
        self.symbol_map = symbol_map
        self.on_ticker = on_ticker
        self._backoff = self.INITIAL_BACKOFF
        self._running = False
        self._ws = None

    @property
    @abstractmethod
    def exchange(self) -> Exchange: ...

    @property
    @abstractmethod
    def ws_url(self) -> str: ...

    @abstractmethod
    def _build_subscribe_message(self) -> dict | list[dict]: ...

    @abstractmethod
    def _parse_message(self, raw: dict) -> Ticker | None: ...

    async def start(self) -> None:
        """Connect and listen, reconnecting on failure."""
        import websockets

        self._running = True
        while self._running:
            try:
                logger.info(
                    "[%s] Connecting to %s", self.exchange.value, self.ws_url
                )
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=self.PING_INTERVAL,
                    ping_timeout=self.PING_TIMEOUT,
                ) as ws:
                    self._ws = ws
                    self._backoff = self.INITIAL_BACKOFF
                    logger.info("[%s] Connected.", self.exchange.value)

                    await self._subscribe(ws)
                    await self._listen(ws)

            except asyncio.CancelledError:
                logger.info("[%s] Task cancelled.", self.exchange.value)
                self._running = False
                return
            except Exception as exc:
                logger.warning(
                    "[%s] Connection lost: %s. Reconnecting in %.1fs...",
                    self.exchange.value,
                    exc,
                    self._backoff,
                )
                await asyncio.sleep(self._backoff)
                self._backoff = min(
                    self._backoff * self.BACKOFF_FACTOR, self.MAX_BACKOFF
                )

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()

    async def _subscribe(self, ws) -> None:
        import json as _json

        msg = self._build_subscribe_message()
        messages = msg if isinstance(msg, list) else [msg]
        for m in messages:
            await ws.send(_json.dumps(m))
            logger.debug("[%s] Sent subscribe: %s", self.exchange.value, m)

    async def _listen(self, ws) -> None:
        import json as _json

        async for raw_msg in ws:
            try:
                data = _json.loads(raw_msg)
                ticker = self._parse_message(data)
                if ticker:
                    await self.on_ticker(ticker)
            except Exception:
                logger.exception(
                    "[%s] Error parsing message", self.exchange.value
                )
