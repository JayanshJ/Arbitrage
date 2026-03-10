"""Phase 1 entrypoint — connects to all exchanges and prints unified ticker data.

Usage:
    python -m src.main
    python -m src.main --symbols BTC-USD ETH-USD
    python -m src.main --exchanges binance kraken
    LOG_LEVEL=DEBUG python -m src.main
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections import defaultdict
from datetime import datetime, timezone

from .models.ticker import Exchange, Ticker
from .models.symbol_map import SymbolMapping
from .exchanges import BinanceWS, KrakenWS, CoinbaseWS

logger = logging.getLogger("arbitrage")

# Latest ticker per (exchange, symbol) — ready for Phase 2 Redis push
latest_tickers: dict[tuple[str, str], Ticker] = {}

# Stats tracking
stats: dict[str, int] = defaultdict(int)


async def on_ticker(ticker: Ticker) -> None:
    """Callback invoked on every ticker update from any exchange."""
    key = (ticker.exchange.value, ticker.symbol)
    latest_tickers[key] = ticker
    stats[ticker.exchange.value] += 1

    total = sum(stats.values())
    if total % 50 == 0:
        print_summary()
    else:
        logger.info("%s", ticker)


def print_summary() -> None:
    """Print a formatted summary of the latest prices across all exchanges."""
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    lines = [f"\n{'='*80}", f"  LIVE TICKER SUMMARY @ {now} UTC", f"{'='*80}"]

    symbols = sorted({sym for _, sym in latest_tickers})
    for symbol in symbols:
        lines.append(f"\n  {symbol}:")
        for exchange in Exchange:
            key = (exchange.value, symbol)
            t = latest_tickers.get(key)
            if t:
                lines.append(
                    f"    {exchange.value:>10s}  "
                    f"bid {t.best_bid:>12,.2f} ({t.bid_qty:.6f})  "
                    f"ask {t.best_ask:>12,.2f} ({t.ask_qty:.6f})  "
                    f"spread {t.spread_bps:>5.1f}bps"
                )
            else:
                lines.append(f"    {exchange.value:>10s}  -- waiting --")

    total = sum(stats.values())
    per_exchange = "  ".join(
        f"{ex.value}={stats[ex.value]}" for ex in Exchange
    )
    lines.append(f"\n  Messages received: {total} ({per_exchange})")
    lines.append(f"{'='*80}\n")
    print("\n".join(lines))


EXCHANGE_CLIENTS = {
    Exchange.BINANCE: BinanceWS,
    Exchange.KRAKEN: KrakenWS,
    Exchange.COINBASE: CoinbaseWS,
}


async def run(
    exchanges: list[Exchange] | None = None,
    symbols: list[str] | None = None,
) -> None:
    symbol_map = SymbolMapping.from_config()

    if symbols:
        # Filter symbol map to only requested symbols
        symbol_map.unified_symbols = [
            s for s in symbol_map.unified_symbols if s in symbols
        ]

    active_exchanges = exchanges or list(Exchange)
    clients = []
    for ex in active_exchanges:
        client_cls = EXCHANGE_CLIENTS[ex]
        clients.append(client_cls(symbol_map=symbol_map, on_ticker=on_ticker))

    logger.info(
        "Starting %d exchange connections for symbols: %s",
        len(clients),
        symbol_map.unified_symbols,
    )

    tasks = [asyncio.create_task(c.start()) for c in clients]

    # Graceful shutdown on SIGINT/SIGTERM
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _shutdown(sig: signal.Signals) -> None:
        logger.info("Received %s, shutting down...", sig.name)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown, sig)

    await stop_event.wait()

    logger.info("Stopping all clients...")
    for c in clients:
        await c.stop()

    for t in tasks:
        t.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)

    print_summary()
    logger.info("Shutdown complete.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Arbitrage data ingestion — Phase 1"
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Unified symbols to track (e.g. BTC-USD ETH-USD). Default: all.",
    )
    parser.add_argument(
        "--exchanges",
        nargs="+",
        default=None,
        choices=[e.value for e in Exchange],
        help="Exchanges to connect to. Default: all.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override LOG_LEVEL env var.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    import os

    level = args.log_level or os.environ.get("LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    exchanges = (
        [Exchange(e) for e in args.exchanges] if args.exchanges else None
    )

    asyncio.run(run(exchanges=exchanges, symbols=args.symbols))


if __name__ == "__main__":
    main()
