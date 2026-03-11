"""Paper trading service.

Receives ArbitrageOpportunity signals from the engine, simulates execution,
and records trades + balance updates in PostgreSQL.

Key rules:
    - Starts with a configurable virtual balance (default $10,000).
    - Each trade uses at most `max_position_pct` of the balance (default 10%).
    - Quantity is capped by both balance and available order book depth.
    - A cooldown prevents trading the same symbol within N seconds.
    - All fees and slippage are deducted from the balance realistically.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncEngine

from ..db.session import get_session
from ..db.models import PaperTrade, Balance, TradeStatus
from .arbitrage import ArbitrageOpportunity

logger = logging.getLogger(__name__)

DEFAULT_INITIAL_BALANCE = 10_000.0
DEFAULT_MAX_POSITION_PCT = 10.0  # max 10% of balance per trade
DEFAULT_COOLDOWN_SECONDS = 30.0  # min seconds between trades on same symbol


@dataclass
class TradeResult:
    """Result of a paper trade execution."""

    trade: PaperTrade
    balance_before: float
    balance_after: float
    skipped: bool = False
    skip_reason: str = ""


class PaperTrader:
    """Simulates arbitrage trade execution against a virtual balance."""

    def __init__(
        self,
        engine: AsyncEngine,
        initial_balance: float = DEFAULT_INITIAL_BALANCE,
        max_position_pct: float = DEFAULT_MAX_POSITION_PCT,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self._engine = engine
        self.balance = initial_balance
        self.max_position_pct = max_position_pct
        self.cooldown_seconds = cooldown_seconds
        self._last_trade_time: dict[str, float] = {}  # symbol -> epoch
        self.total_trades = 0
        self.total_profit = 0.0

    async def initialize(self) -> None:
        """Record the initial balance in the database."""
        async with get_session(self._engine) as session:
            session.add(Balance(
                balance=self.balance,
                reason="initial",
            ))
        logger.info("Paper trader initialized with $%.2f", self.balance)

    def _is_on_cooldown(self, symbol: str) -> bool:
        last = self._last_trade_time.get(symbol, 0)
        return (time.time() - last) < self.cooldown_seconds

    def _calculate_position_size(
        self, opp: ArbitrageOpportunity
    ) -> Optional[float]:
        """Determine how much to trade.

        Returns the quantity to trade, or None if the trade should be skipped.
        """
        max_usd = self.balance * (self.max_position_pct / 100)
        max_qty_by_balance = max_usd / opp.buy_price if opp.buy_price > 0 else 0

        # Cap by available order book depth
        qty = min(max_qty_by_balance, opp.max_tradeable_qty)

        if qty <= 0:
            return None

        # Ensure we can afford the buy + fees
        total_cost = qty * opp.buy_price * (1 + opp.total_fees_pct / 100)
        if total_cost > self.balance:
            qty = self.balance / (opp.buy_price * (1 + opp.total_fees_pct / 100))

        if qty <= 0:
            return None

        return qty

    async def execute(self, opp: ArbitrageOpportunity) -> TradeResult:
        """Execute a paper trade for the given opportunity.

        Returns a TradeResult with the trade details and updated balance.
        """
        # Check cooldown
        if self._is_on_cooldown(opp.symbol):
            return TradeResult(
                trade=None,
                balance_before=self.balance,
                balance_after=self.balance,
                skipped=True,
                skip_reason=f"Cooldown active for {opp.symbol}",
            )

        # Check minimum profit threshold
        if opp.net_profit_pct <= 0:
            return TradeResult(
                trade=None,
                balance_before=self.balance,
                balance_after=self.balance,
                skipped=True,
                skip_reason="Net profit <= 0 after fees",
            )

        # Calculate position size
        qty = self._calculate_position_size(opp)
        if qty is None or qty <= 0:
            return TradeResult(
                trade=None,
                balance_before=self.balance,
                balance_after=self.balance,
                skipped=True,
                skip_reason="Insufficient balance or zero quantity",
            )

        balance_before = self.balance

        # Simulate execution
        buy_cost = qty * opp.buy_price
        sell_revenue = qty * opp.sell_price
        buy_fee = buy_cost * (opp.total_fees_pct / 2) / 100  # split fees
        sell_fee = sell_revenue * (opp.total_fees_pct / 2) / 100
        slippage_cost = buy_cost * (opp.slippage_pct / 100)

        gross_profit = sell_revenue - buy_cost
        net_profit = gross_profit - buy_fee - sell_fee - slippage_cost

        self.balance += net_profit
        self.total_trades += 1
        self.total_profit += net_profit
        self._last_trade_time[opp.symbol] = time.time()

        # Record in database
        trade = PaperTrade(
            symbol=opp.symbol,
            quantity=qty,
            buy_exchange=opp.buy_exchange.value,
            buy_price=opp.buy_price,
            buy_cost=buy_cost,
            buy_fee=buy_fee,
            sell_exchange=opp.sell_exchange.value,
            sell_price=opp.sell_price,
            sell_revenue=sell_revenue,
            sell_fee=sell_fee,
            slippage_cost=slippage_cost,
            gross_profit=gross_profit,
            net_profit=net_profit,
            net_profit_pct=opp.net_profit_pct,
            balance_after=self.balance,
            status=TradeStatus.EXECUTED,
        )

        async with get_session(self._engine) as session:
            session.add(trade)
            await session.flush()

            session.add(Balance(
                balance=self.balance,
                trade_id=trade.id,
                reason="trade",
            ))

        logger.info(
            "TRADE #%d: %s BUY %s @ $%.2f, SELL %s @ $%.2f | "
            "qty=%.6f net=$%+.2f balance=$%.2f",
            self.total_trades,
            opp.symbol,
            opp.buy_exchange.value,
            opp.buy_price,
            opp.sell_exchange.value,
            opp.sell_price,
            qty,
            net_profit,
            self.balance,
        )

        return TradeResult(
            trade=trade,
            balance_before=balance_before,
            balance_after=self.balance,
        )
