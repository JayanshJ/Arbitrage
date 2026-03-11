"""FastAPI server providing REST + SSE endpoints for the dashboard.

Endpoints:
    GET  /api/trades          — paginated trade history
    GET  /api/balances        — balance snapshots for PnL chart
    GET  /api/stats           — current system stats
    GET  /api/spreads/stream  — SSE stream of real-time spread data
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, desc

from ..db.session import get_session, get_engine
from ..db.models import PaperTrade, Balance
from ..cache.redis_cache import RedisCache
from ..models.ticker import Exchange

logger = logging.getLogger(__name__)

app = FastAPI(title="Arbitrage Dashboard API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# These get set by the main process when starting the API server
_cache: Optional[RedisCache] = None
_trader = None  # PaperTrader instance


def set_dependencies(cache: RedisCache, trader) -> None:
    global _cache, _trader
    _cache = cache
    _trader = trader


@app.get("/api/trades")
async def get_trades(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Return paginated trade history, newest first."""
    engine = get_engine()
    async with get_session(engine) as session:
        # Total count
        count_result = await session.execute(
            select(func.count(PaperTrade.id))
        )
        total = count_result.scalar() or 0

        # Paginated trades
        result = await session.execute(
            select(PaperTrade)
            .order_by(desc(PaperTrade.created_at))
            .limit(limit)
            .offset(offset)
        )
        trades = result.scalars().all()

    return {
        "total": total,
        "trades": [
            {
                "id": t.id,
                "symbol": t.symbol,
                "quantity": t.quantity,
                "buy_exchange": t.buy_exchange,
                "buy_price": t.buy_price,
                "buy_cost": t.buy_cost,
                "buy_fee": t.buy_fee,
                "sell_exchange": t.sell_exchange,
                "sell_price": t.sell_price,
                "sell_revenue": t.sell_revenue,
                "sell_fee": t.sell_fee,
                "slippage_cost": t.slippage_cost,
                "gross_profit": t.gross_profit,
                "net_profit": t.net_profit,
                "net_profit_pct": t.net_profit_pct,
                "balance_after": t.balance_after,
                "status": t.status.value if t.status else "executed",
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in trades
        ],
    }


@app.get("/api/balances")
async def get_balances(
    limit: int = Query(200, ge=1, le=1000),
):
    """Return balance history for PnL charting."""
    engine = get_engine()
    async with get_session(engine) as session:
        result = await session.execute(
            select(Balance)
            .order_by(Balance.created_at)
            .limit(limit)
        )
        balances = result.scalars().all()

    return {
        "balances": [
            {
                "id": b.id,
                "balance": b.balance,
                "trade_id": b.trade_id,
                "reason": b.reason,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in balances
        ],
    }


@app.get("/api/stats")
async def get_stats():
    """Return current system statistics."""
    balance = _trader.balance if _trader else 0
    total_trades = _trader.total_trades if _trader else 0
    total_profit = _trader.total_profit if _trader else 0
    initial = 10_000.0

    # Get latest tickers from Redis
    tickers = {}
    if _cache:
        symbols = ["BTC-USD", "ETH-USD", "SOL-USD"]
        for symbol in symbols:
            symbol_tickers = await _cache.get_all_tickers_for_symbol(symbol)
            if symbol_tickers:
                tickers[symbol] = {
                    ex.value: {
                        "bid": t.best_bid,
                        "ask": t.best_ask,
                        "bid_qty": t.bid_qty,
                        "ask_qty": t.ask_qty,
                        "spread_bps": t.spread_bps,
                    }
                    for ex, t in symbol_tickers.items()
                }

    return {
        "balance": balance,
        "initial_balance": initial,
        "total_trades": total_trades,
        "total_profit": total_profit,
        "pnl_pct": ((balance - initial) / initial * 100) if initial > 0 else 0,
        "tickers": tickers,
    }


@app.get("/api/spreads/stream")
async def stream_spreads():
    """SSE endpoint streaming real-time spread data from Redis.

    Polls Redis every 500ms and sends the current cross-exchange
    spread for each symbol.
    """

    async def event_generator():
        symbols = ["BTC-USD", "ETH-USD", "SOL-USD"]
        while True:
            if not _cache:
                await asyncio.sleep(1)
                continue

            spreads = {}
            for symbol in symbols:
                tickers = await _cache.get_all_tickers_for_symbol(symbol)
                if len(tickers) < 2:
                    continue

                # Calculate best spread across all exchange pairs
                exchanges = list(tickers.keys())
                best_spread = None
                for i, ex_a in enumerate(exchanges):
                    for ex_b in exchanges[i + 1 :]:
                        t_a, t_b = tickers[ex_a], tickers[ex_b]

                        # Direction 1: buy A sell B
                        s1 = (t_b.best_bid - t_a.best_ask) / t_a.best_ask * 100
                        # Direction 2: buy B sell A
                        s2 = (t_a.best_bid - t_b.best_ask) / t_b.best_ask * 100

                        spread = max(s1, s2)
                        if best_spread is None or spread > best_spread["spread_pct"]:
                            if s1 >= s2:
                                best_spread = {
                                    "spread_pct": round(s1, 4),
                                    "buy_exchange": ex_a.value,
                                    "sell_exchange": ex_b.value,
                                    "buy_price": t_a.best_ask,
                                    "sell_price": t_b.best_bid,
                                }
                            else:
                                best_spread = {
                                    "spread_pct": round(s2, 4),
                                    "buy_exchange": ex_b.value,
                                    "sell_exchange": ex_a.value,
                                    "buy_price": t_b.best_ask,
                                    "sell_price": t_a.best_bid,
                                }

                if best_spread:
                    spreads[symbol] = best_spread

            if spreads:
                data = json.dumps({
                    "timestamp": time.time(),
                    "spreads": spreads,
                    "balance": _trader.balance if _trader else 0,
                    "total_trades": _trader.total_trades if _trader else 0,
                    "total_profit": _trader.total_profit if _trader else 0,
                })
                yield f"data: {data}\n\n"

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
