"""FastAPI server — pairs trading REST + SSE endpoints.

Endpoints:
    GET  /api/pairs/status    — current z-scores, signals, open positions
    GET  /api/pairs/trades    — paginated pairs trade history
    GET  /api/pairs/stream    — SSE stream of real-time z-score data (1 s)
    GET  /api/risk            — risk manager state
    POST /api/risk/reset-halt — manually clear a risk halt
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, desc

from ..db.session import get_session, get_engine
from ..db.models import PairsTrade

logger = logging.getLogger(__name__)

app = FastAPI(title="Pairs Trading API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_pairs_engine = None   # PairsEngine instance


def set_dependencies(pairs_engine) -> None:
    global _pairs_engine
    _pairs_engine = pairs_engine


# ---------------------------------------------------------------------------
# Pairs status
# ---------------------------------------------------------------------------

@app.get("/api/pairs/status")
async def get_pairs_status():
    """Current z-scores, signals, and open positions for all pairs."""
    if not _pairs_engine:
        return {"pairs": [], "pairs_balance": 0,
                "pairs_total_trades": 0, "pairs_total_profit": 0}
    return {
        "pairs": _pairs_engine.get_status(),
        "pairs_balance": _pairs_engine.balance,
        "pairs_initial_balance": _pairs_engine.initial_balance,
        "pairs_total_trades": _pairs_engine.total_trades,
        "pairs_total_profit": _pairs_engine.total_profit,
    }


# ---------------------------------------------------------------------------
# Pairs trade history
# ---------------------------------------------------------------------------

@app.get("/api/pairs/trades")
async def get_pairs_trades(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Paginated pairs trade history, newest first."""
    engine = get_engine()
    async with get_session(engine) as session:
        total = (await session.execute(
            select(func.count(PairsTrade.id))
        )).scalar() or 0

        trades = (await session.execute(
            select(PairsTrade)
            .order_by(desc(PairsTrade.created_at))
            .limit(limit)
            .offset(offset)
        )).scalars().all()

    return {
        "total": total,
        "trades": [
            {
                "id": t.id,
                "pair_id": t.pair_id,
                "symbol_a": t.symbol_a,
                "symbol_b": t.symbol_b,
                "direction": t.direction,
                "entry_z_score": t.entry_z_score,
                "entry_price_a": t.entry_price_a,
                "entry_price_b": t.entry_price_b,
                "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                "notional_usd": t.notional_usd,
                "hedge_ratio": t.hedge_ratio,
                "half_life_hours": t.half_life_hours,
                "qty_a": t.qty_a,
                "qty_b": t.qty_b,
                "exit_z_score": t.exit_z_score,
                "exit_price_a": t.exit_price_a,
                "exit_price_b": t.exit_price_b,
                "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                "pnl_a": t.pnl_a,
                "pnl_b": t.pnl_b,
                "net_pnl": t.net_pnl,
                "hold_seconds": t.hold_seconds,
                "pairs_balance_after": t.pairs_balance_after,
                "close_reason": t.close_reason,
                "status": t.status,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in trades
        ],
    }


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------

@app.get("/api/pairs/stream")
async def stream_pairs():
    """SSE: real-time z-score + balance data, pushed every second."""

    async def event_generator():
        while True:
            if not _pairs_engine:
                await asyncio.sleep(1)
                continue

            data = json.dumps({
                "timestamp": time.time(),
                "pairs": _pairs_engine.get_status(),
                "pairs_balance": _pairs_engine.balance,
                "pairs_initial_balance": _pairs_engine.initial_balance,
                "pairs_total_trades": _pairs_engine.total_trades,
                "pairs_total_profit": _pairs_engine.total_profit,
                "risk": _pairs_engine.risk.status_dict(),
            })
            yield f"data: {data}\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Risk endpoints
# ---------------------------------------------------------------------------

@app.get("/api/risk")
async def get_risk():
    """Current risk manager state: halt status, drawdown, limits."""
    if not _pairs_engine:
        return {"halted": False, "halt_reason": None}
    return _pairs_engine.risk.status_dict()


@app.post("/api/risk/reset-halt")
async def reset_halt():
    """Manually clear a risk halt AFTER investigating the root cause."""
    if not _pairs_engine:
        return {"ok": False, "reason": "pairs engine not running"}
    _pairs_engine.risk.reset_halt()
    return {"ok": True}
