"""Telegram alert system for the pairs trading engine.

Sends real-time notifications to your phone when trades open/close,
risk limits trigger, or the system halts.

Setup:
    1. Create a bot via @BotFather on Telegram → get TELEGRAM_BOT_TOKEN
    2. Get your chat ID via @userinfobot → TELEGRAM_CHAT_ID
    3. Export both as environment variables before starting the backend

    export TELEGRAM_BOT_TOKEN="123456:ABC..."
    export TELEGRAM_CHAT_ID="987654321"

If either env var is missing, the alert system is silently disabled —
the rest of the engine runs normally.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_BOT_TOKEN: Optional[str] = os.environ.get("TELEGRAM_BOT_TOKEN")
_CHAT_ID:   Optional[str] = os.environ.get("TELEGRAM_CHAT_ID")
_ENABLED = bool(_BOT_TOKEN and _CHAT_ID)

if not _ENABLED:
    logger.info(
        "Telegram alerts disabled (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID to enable)"
    )


class TelegramAlerts:
    """Async Telegram notifier.  All methods are safe to call even when
    Telegram is not configured — they become no-ops."""

    def __init__(self) -> None:
        self.enabled = _ENABLED
        self._base = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"

    async def _send(self, text: str) -> None:
        if not self.enabled:
            return
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    self._base,
                    json={
                        "chat_id": _CHAT_ID,
                        "text": text,
                        "parse_mode": "HTML",
                    },
                    timeout=aiohttp.ClientTimeout(total=5),
                )
        except Exception as exc:
            logger.warning("Telegram alert failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Public notification methods                                          #
    # ------------------------------------------------------------------ #

    async def on_trade_open(
        self,
        pair_id: str,
        direction: str,
        z_score: float,
        notional_usd: float,
        hedge_ratio: float,
        half_life_h: float,
    ) -> None:
        arrow = "↑A ↓B" if direction == "long_a_short_b" else "↓A ↑B"
        await self._send(
            f"📈 <b>PAIRS OPEN</b>  {pair_id}\n"
            f"Direction: {arrow}\n"
            f"Z-score: <b>{z_score:+.2f}σ</b>\n"
            f"Notional: ${notional_usd:,.0f}/leg\n"
            f"Hedge β: {hedge_ratio:.3f}  |  t½: {half_life_h:.1f} h"
        )

    async def on_trade_close(
        self,
        pair_id: str,
        net_pnl: float,
        hold_hours: float,
        close_reason: str,
        exit_z: float,
    ) -> None:
        emoji = "✅" if net_pnl > 0 else "❌"
        await self._send(
            f"{emoji} <b>PAIRS CLOSE</b>  {pair_id}\n"
            f"Net P&L: <b>${net_pnl:+.2f}</b>\n"
            f"Exit z: {exit_z:+.2f}σ  |  Held: {hold_hours:.1f} h\n"
            f"Reason: {close_reason}"
        )

    async def on_halt(self, reason: str) -> None:
        await self._send(
            f"🚨 <b>RISK HALT</b>\n"
            f"{reason}\n\n"
            f"All trading stopped. Check the dashboard immediately."
        )

    async def on_pair_disabled(self, pair_id: str, reason: str) -> None:
        await self._send(
            f"⚠️ <b>PAIR DISABLED</b>  {pair_id}\n"
            f"Failed cointegration re-check: {reason}"
        )

    async def on_startup(self, pairs: list[str], balance: float) -> None:
        await self._send(
            f"🤖 <b>Pairs Engine Started</b>\n"
            f"Pairs: {', '.join(pairs)}\n"
            f"Balance: ${balance:,.2f}"
        )
