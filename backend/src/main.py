"""Entrypoint — connects to exchanges and runs the pairs trading engine.

Usage:
    python -m src.main                       # normal run
    python -m src.main --record              # normal run + record ticks to CSV
    python -m src.main --record-only         # ONLY record, do NOT trade (safe data collection)
    python -m src.main --symbols BTC-USD ETH-USD SOL-USD
    python -m src.main --exchanges binance kraken
    LOG_LEVEL=DEBUG python -m src.main
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import signal
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models.ticker import Exchange
from .models.symbol_map import SymbolMapping
from .exchanges import BinanceWS, KrakenWS, CoinbaseWS
from .engine.pairs_engine import PairsEngine
from .engine.real_costs import RealCostModel
from .engine.risk_manager import RiskManager
from .alerts.telegram_alerts import TelegramAlerts
from .db.session import init_db, close_db
from .api.server import app as api_app, set_dependencies

logger = logging.getLogger("arbitrage")

_tick_stats: dict[str, int] = defaultdict(int)
_pairs_engine: Optional[PairsEngine] = None
_symbol_map: Optional[SymbolMapping] = None

# ---------------------------------------------------------------------------
# Live tick recorder
# ---------------------------------------------------------------------------

class TickRecorder:
    """Writes mid-prices to a daily rotating CSV for offline analysis.

    File path: <data_dir>/ticks_YYYY-MM-DD.csv
    Columns  : timestamp_utc, symbol, exchange, mid_price, best_bid, best_ask
    """

    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = None
        self._writer = None
        self._current_date: str = ""
        self._rows_written = 0
        self._rotate()

    def _rotate(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today == self._current_date:
            return
        self._current_date = today
        path = self._dir / f"ticks_{today}.csv"
        is_new = not path.exists()
        if self._file:
            self._file.close()
        self._file = path.open("a", newline="", buffering=1)  # line-buffered
        self._writer = csv.writer(self._file)
        if is_new:
            self._writer.writerow(
                ["timestamp_utc", "symbol", "exchange", "mid_price",
                 "best_bid", "best_ask"]
            )
        logger.info("TickRecorder → %s", path)

    def record(self, ticker) -> None:
        self._rotate()
        self._writer.writerow([
            datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            ticker.symbol,
            ticker.exchange.value,
            ticker.mid_price,
            ticker.best_bid,
            ticker.best_ask,
        ])
        self._rows_written += 1
        if self._rows_written % 10_000 == 0:
            logger.info("TickRecorder: %d rows written", self._rows_written)

    def close(self) -> None:
        if self._file:
            self._file.close()
            logger.info(
                "TickRecorder closed — %d total rows written.", self._rows_written
            )


_recorder: Optional[TickRecorder] = None


# ---------------------------------------------------------------------------
# Ticker callback
# ---------------------------------------------------------------------------

async def on_ticker(ticker) -> None:
    _tick_stats[ticker.exchange.value] += 1

    if _recorder:
        _recorder.record(ticker)

    if _pairs_engine:
        await _pairs_engine.on_ticker(ticker.symbol, ticker.mid_price)

    if sum(_tick_stats.values()) % 500 == 0:
        _log_status()


def _log_status() -> None:
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    total = sum(_tick_stats.values())
    per_ex = "  ".join(f"{ex.value}={_tick_stats[ex.value]}" for ex in Exchange)
    pairs_info = ""
    if _pairs_engine:
        pairs_info = (
            f" | trades={_pairs_engine.total_trades}"
            f"  P&L=${_pairs_engine.total_profit:+,.2f}"
            f"  balance=${_pairs_engine.balance:,.2f}"
        )
    rec_info = f"  | recorded={_recorder._rows_written:,}" if _recorder else ""
    logger.info("[%s] ticks=%d (%s)%s%s", now, total, per_ex, pairs_info, rec_info)


EXCHANGE_CLIENTS = {
    Exchange.BINANCE: BinanceWS,
    Exchange.KRAKEN: KrakenWS,
    Exchange.COINBASE: CoinbaseWS,
}


# ---------------------------------------------------------------------------
# Main run loop
# ---------------------------------------------------------------------------

async def run(
    exchanges: list[Exchange] | None = None,
    symbols: list[str] | None = None,
    db_url: str = "postgresql+asyncpg://localhost/arbitrage",
    initial_balance: float = 5_000.0,
    api_port: int = 8000,
    record: bool = False,
    record_only: bool = False,
    data_dir: Path = Path("data/ticks"),
) -> None:
    global _pairs_engine, _symbol_map, _recorder

    _symbol_map = SymbolMapping.from_config()
    if symbols:
        _symbol_map.unified_symbols = [
            s for s in _symbol_map.unified_symbols if s in symbols
        ]

    # --- Tick recorder (optional) ---
    if record or record_only:
        _recorder = TickRecorder(data_dir)

    # --- Database + Pairs engine (skipped in record-only mode) ---
    if not record_only:
        try:
            db_engine = await init_db(db_url)
            risk_mgr = RiskManager(initial_capital=initial_balance)
            alerts = TelegramAlerts()
            risk_mgr.set_alerts(alerts)
            _pairs_engine = PairsEngine(
                db_engine=db_engine,
                risk_manager=risk_mgr,
                cost_model=RealCostModel(),
                alerts=alerts,
                initial_balance=initial_balance,
            )
            await _pairs_engine.initialize()
            logger.info("Pairs engine ready — balance=$%.2f", initial_balance)
        except Exception as exc:
            logger.error("DB init failed: %s — running without persistence.", exc)
            _pairs_engine = None

    # --- Exchange WebSocket clients ---
    active_exchanges = exchanges or list(Exchange)
    clients = [
        EXCHANGE_CLIENTS[ex](symbol_map=_symbol_map, on_ticker=on_ticker)
        for ex in active_exchanges
    ]
    logger.info(
        "Connecting to %d exchange(s): %s",
        len(clients), _symbol_map.unified_symbols,
    )
    tasks = [asyncio.create_task(c.start()) for c in clients]

    # --- API server (skipped in record-only mode) ---
    if not record_only:
        import uvicorn
        set_dependencies(_pairs_engine)
        api_server = uvicorn.Server(
            uvicorn.Config(api_app, host="0.0.0.0", port=api_port, log_level="warning")
        )
        tasks.append(asyncio.create_task(api_server.serve()))

    # --- Graceful shutdown ---
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _shutdown(sig: signal.Signals) -> None:
        logger.info("Received %s — shutting down.", sig.name)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown, sig)

    mode = "RECORD-ONLY (no trading)" if record_only else (
        "LIVE TRADING" + (" + recording" if record else "")
    )
    print(
        f"\n{'='*60}\n"
        f"  Statistical Arbitrage — Pairs Trading Engine\n"
        f"  Mode      : {mode}\n"
        f"  Exchanges : {', '.join(e.value for e in active_exchanges)}\n"
        f"  Symbols   : {', '.join(_symbol_map.unified_symbols)}\n"
        f"  Database  : {'connected' if _pairs_engine else ('N/A' if record_only else 'NOT CONNECTED')}\n"
        f"  Balance   : ${initial_balance:,.2f}\n"
        + (f"  Recording : {data_dir.resolve()}/\n" if _recorder else "")
        + (f"  API       : http://localhost:{api_port}\n" if not record_only else "")
        + f"  Press Ctrl+C to stop\n"
        f"{'='*60}\n"
    )

    await stop_event.wait()

    logger.info("Stopping...")
    for c in clients:
        await c.stop()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    if _recorder:
        _recorder.close()

    if not record_only:
        await close_db()

    logger.info("Shutdown complete.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pairs Trading Engine")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--exchanges", nargs="+", default=None,
                        choices=[e.value for e in Exchange])
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--balance", type=float, default=None)
    parser.add_argument("--api-port", type=int, default=None)
    parser.add_argument("--log-level", default=None,
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument(
        "--record", action="store_true",
        help="Record live ticks to CSV while trading (for later analysis)",
    )
    parser.add_argument(
        "--record-only", action="store_true",
        help="ONLY record ticks to CSV — no trading, no DB, no API. "
             "Safe data collection mode.",
    )
    parser.add_argument(
        "--data-dir", type=Path,
        default=Path("data/ticks"),
        help="Directory to write tick CSVs (default: data/ticks/)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    level = args.log_level or os.environ.get("LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    exchanges = [Exchange(e) for e in args.exchanges] if args.exchanges else None
    db_url = args.db_url or os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://localhost/arbitrage"
    )
    balance = args.balance or float(os.environ.get("INITIAL_BALANCE", "5000"))
    api_port = args.api_port or int(os.environ.get("API_PORT", "8000"))

    asyncio.run(run(
        exchanges=exchanges,
        symbols=args.symbols,
        db_url=db_url,
        initial_balance=balance,
        api_port=api_port,
        record=args.record,
        record_only=args.record_only,
        data_dir=args.data_dir,
    ))


if __name__ == "__main__":
    main()
