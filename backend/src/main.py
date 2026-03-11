"""Entrypoint — connects to exchanges, caches in Redis, detects arbitrage,
and executes paper trades.

Usage:
    python -m src.main
    python -m src.main --symbols BTC-USD ETH-USD
    python -m src.main --exchanges binance kraken
    python -m src.main --redis-url redis://localhost:6379/0
    python -m src.main --db-url postgresql+asyncpg://localhost/arbitrage
    python -m src.main --balance 50000
    LOG_LEVEL=DEBUG python -m src.main
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from .models.ticker import Exchange, Ticker
from .models.symbol_map import SymbolMapping
from .exchanges import BinanceWS, KrakenWS, CoinbaseWS
from .cache.redis_cache import RedisCache
from .engine.arbitrage import ArbitrageEngine, ArbitrageOpportunity
from .engine.paper_trader import PaperTrader
from .engine.pairs_engine import PairsEngine
from .engine.real_costs import RealCostModel
from .engine.risk_manager import RiskManager
from .alerts.telegram_alerts import TelegramAlerts
from .db.session import init_db, close_db
from .api.server import app as api_app, set_dependencies

logger = logging.getLogger("arbitrage")

# Stats tracking
stats: dict[str, int] = defaultdict(int)
opp_count = 0

# Globals set during run()
_cache: Optional[RedisCache] = None
_engine: Optional[ArbitrageEngine] = None
_trader: Optional[PaperTrader] = None
_pairs_engine: Optional[PairsEngine] = None
_symbol_map: Optional[SymbolMapping] = None


async def on_ticker(ticker: Ticker) -> None:
    """Callback invoked on every ticker update from any exchange.

    Pipeline: cache ticker -> scan for arbitrage -> execute paper trade
              -> feed pairs engine for statistical arbitrage.
    """
    global opp_count

    stats[ticker.exchange.value] += 1

    if _cache:
        await _cache.set_ticker(ticker)

    if _engine:
        opps = await _engine.scan_symbol(ticker.symbol)
        for opp in opps:
            opp_count += 1
            _print_opportunity(opp)

            # Execute paper trade
            if _trader:
                result = await _trader.execute(opp)
                if not result.skipped:
                    _print_trade(result)
                elif result.skip_reason:
                    logger.debug("Trade skipped: %s", result.skip_reason)

    # Feed mid-price to pairs engine for statistical arbitrage
    if _pairs_engine:
        await _pairs_engine.on_ticker(ticker.symbol, ticker.mid_price)

    total = sum(stats.values())
    if total % 200 == 0:
        _print_status()


def _print_opportunity(opp: ArbitrageOpportunity) -> None:
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(
        f"\n{'*'*70}\n"
        f"  ARBITRAGE #{opp_count} DETECTED @ {now} UTC\n"
        f"  {opp.symbol}: BUY on {opp.buy_exchange.value} @ "
        f"${opp.buy_price:,.2f} -> SELL on {opp.sell_exchange.value} @ "
        f"${opp.sell_price:,.2f}\n"
        f"  Gross: {opp.gross_profit_pct:+.4f}%  "
        f"Fees: -{opp.total_fees_pct:.4f}%  "
        f"Slippage: -{opp.slippage_pct:.4f}%  "
        f"NET: {opp.net_profit_pct:+.4f}%\n"
        f"  Max qty: {opp.max_tradeable_qty:.6f}  "
        f"Est. profit: ${opp.estimated_profit_usd:,.2f}\n"
        f"{'*'*70}"
    )


def _print_trade(result) -> None:
    t = result.trade
    print(
        f"  >> PAPER TRADE EXECUTED: {t.symbol}\n"
        f"     Buy {t.quantity:.6f} on {t.buy_exchange} @ ${t.buy_price:,.2f}\n"
        f"     Sell on {t.sell_exchange} @ ${t.sell_price:,.2f}\n"
        f"     Net P&L: ${t.net_profit:+,.2f} | "
        f"Balance: ${result.balance_before:,.2f} -> ${result.balance_after:,.2f}"
    )


def _print_status() -> None:
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    total = sum(stats.values())
    per_exchange = "  ".join(
        f"{ex.value}={stats[ex.value]}" for ex in Exchange
    )
    trader_info = ""
    if _trader:
        trader_info = (
            f" | Trades: {_trader.total_trades} "
            f"| P&L: ${_trader.total_profit:+,.2f} "
            f"| Balance: ${_trader.balance:,.2f}"
        )
    print(
        f"[{now}] Messages: {total} ({per_exchange}) | "
        f"Opportunities: {opp_count}{trader_info}"
    )


EXCHANGE_CLIENTS = {
    Exchange.BINANCE: BinanceWS,
    Exchange.KRAKEN: KrakenWS,
    Exchange.COINBASE: CoinbaseWS,
}


async def run(
    exchanges: list[Exchange] | None = None,
    symbols: list[str] | None = None,
    redis_url: str = "redis://localhost:6379/0",
    db_url: str = "postgresql+asyncpg://localhost/arbitrage",
    initial_balance: float = 10_000.0,
    api_port: int = 8000,
) -> None:
    global _cache, _engine, _trader, _pairs_engine, _symbol_map

    _symbol_map = SymbolMapping.from_config()

    if symbols:
        _symbol_map.unified_symbols = [
            s for s in _symbol_map.unified_symbols if s in symbols
        ]

    # Connect to Redis
    try:
        _cache = await RedisCache.connect(redis_url)
    except Exception as exc:
        logger.error(
            "Failed to connect to Redis at %s: %s. "
            "Running without caching/arbitrage detection.",
            redis_url, exc,
        )
        _cache = None

    # Initialize arbitrage engine
    if _cache:
        _engine = ArbitrageEngine.from_config(_cache)
        logger.info(
            "Arbitrage engine started (min_profit=%.2f%%, slippage=%.2f%%)",
            _engine.min_profit_pct, _engine.slippage_pct,
        )
    else:
        _engine = None

    # Initialize database + paper trader + pairs engine
    try:
        db_engine = await init_db(db_url)
        _trader = PaperTrader(
            engine=db_engine,
            initial_balance=initial_balance,
        )
        await _trader.initialize()

        # Pairs engine: separate $5k balance with full risk + cost model
        pairs_balance = 5_000.0
        risk_mgr = RiskManager(initial_capital=pairs_balance)
        alerts = TelegramAlerts()
        risk_mgr.set_alerts(alerts)

        _pairs_engine = PairsEngine(
            db_engine=db_engine,
            risk_manager=risk_mgr,
            cost_model=RealCostModel(),
            alerts=alerts,
            initial_balance=pairs_balance,
        )
        await _pairs_engine.initialize()
    except Exception as exc:
        logger.error(
            "Failed to connect to database at %s: %s. "
            "Running without paper trading.",
            db_url, exc,
        )
        _trader = None
        _pairs_engine = None

    # Start exchange WebSocket clients
    active_exchanges = exchanges or list(Exchange)
    clients = []
    for ex in active_exchanges:
        client_cls = EXCHANGE_CLIENTS[ex]
        clients.append(client_cls(symbol_map=_symbol_map, on_ticker=on_ticker))

    logger.info(
        "Starting %d exchange connections for symbols: %s",
        len(clients), _symbol_map.unified_symbols,
    )

    tasks = [asyncio.create_task(c.start()) for c in clients]

    # Start API server
    import uvicorn

    set_dependencies(_cache, _trader, _pairs_engine)
    config = uvicorn.Config(
        api_app, host="0.0.0.0", port=api_port,
        log_level="warning",
    )
    api_server = uvicorn.Server(config)
    api_task = asyncio.create_task(api_server.serve())
    tasks.append(api_task)

    # Graceful shutdown
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _shutdown(sig: signal.Signals) -> None:
        logger.info("Received %s, shutting down...", sig.name)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown, sig)

    print(
        f"\n{'='*70}\n"
        f"  Arbitrage Paper Trading System\n"
        f"  Exchanges: {', '.join(e.value for e in active_exchanges)}\n"
        f"  Symbols: {', '.join(_symbol_map.unified_symbols)}\n"
        f"  Redis: {'connected' if _cache else 'NOT CONNECTED'}\n"
        f"  Database: {'connected' if _trader else 'NOT CONNECTED'}\n"
        f"  API Server: http://localhost:{api_port}\n"
        f"  Cross-Exchange Arb Balance: ${initial_balance:,.2f}\n"
        f"  Statistical Arb (Pairs) Balance: $5,000.00\n"
        f"  Press Ctrl+C to stop\n"
        f"{'='*70}\n"
    )

    await stop_event.wait()

    logger.info("Stopping all clients...")
    for c in clients:
        await c.stop()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    if _cache:
        await _cache.close()
    await close_db()

    _print_status()
    logger.info("Shutdown complete.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Arbitrage Paper Trading System"
    )
    parser.add_argument(
        "--symbols", nargs="+", default=None,
        help="Unified symbols to track (e.g. BTC-USD ETH-USD). Default: all.",
    )
    parser.add_argument(
        "--exchanges", nargs="+", default=None,
        choices=[e.value for e in Exchange],
        help="Exchanges to connect to. Default: all.",
    )
    parser.add_argument(
        "--redis-url", default=None,
        help="Redis URL. Default: redis://localhost:6379/0",
    )
    parser.add_argument(
        "--db-url", default=None,
        help="PostgreSQL URL. Default: postgresql+asyncpg://localhost/arbitrage",
    )
    parser.add_argument(
        "--balance", type=float, default=None,
        help="Initial virtual balance in USD. Default: 10000.",
    )
    parser.add_argument(
        "--api-port", type=int, default=None,
        help="API server port. Default: 8000.",
    )
    parser.add_argument(
        "--log-level", default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override LOG_LEVEL env var.",
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

    exchanges = (
        [Exchange(e) for e in args.exchanges] if args.exchanges else None
    )
    redis_url = args.redis_url or os.environ.get(
        "REDIS_URL", "redis://localhost:6379/0"
    )
    db_url = args.db_url or os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://localhost/arbitrage"
    )
    balance = args.balance or float(os.environ.get("INITIAL_BALANCE", "10000"))
    api_port = args.api_port or int(os.environ.get("API_PORT", "8000"))

    asyncio.run(run(
        exchanges=exchanges,
        symbols=args.symbols,
        redis_url=redis_url,
        db_url=db_url,
        initial_balance=balance,
        api_port=api_port,
    ))


if __name__ == "__main__":
    main()
