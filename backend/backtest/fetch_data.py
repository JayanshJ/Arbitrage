"""Download historical OHLCV klines from Binance public REST API.

Usage (from repo root):
    python -m backend.backtest.fetch_data --pairs ETH/USDT SOL/USDT BTC/USDT \
        --interval 1h --days 90 --out backend/backtest/data/

Outputs one CSV per symbol:  data/<SYMBOL>_<interval>.csv
Columns: timestamp (UTC ISO), open, high, low, close, volume

No API key required — uses Binance's public /api/v3/klines endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

BINANCE_BASE = "https://api.binance.com"
MAX_KLINES_PER_REQUEST = 1000  # Binance hard limit


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


async def fetch_klines(
    session: aiohttp.ClientSession,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[list]:
    """Fetch all klines for a symbol between start_ms and end_ms (inclusive)."""
    url = f"{BINANCE_BASE}/api/v3/klines"
    rows: list[list] = []
    current_start = start_ms

    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": MAX_KLINES_PER_REQUEST,
        }
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

        if not data:
            break

        rows.extend(data)
        # Next page starts just after the last kline's open time
        current_start = data[-1][0] + 1

        if len(data) < MAX_KLINES_PER_REQUEST:
            break  # We got the last page

        await asyncio.sleep(0.1)  # Be polite to the API

    return rows


def klines_to_csv(rows: list[list], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for row in rows:
            writer.writerow([
                _ms_to_iso(row[0]),  # open time
                row[1],              # open
                row[2],              # high
                row[3],              # low
                row[4],              # close
                row[5],              # volume
            ])
    logger.info("Wrote %d rows → %s", len(rows), out_path)


async def main(
    pairs: list[str],
    interval: str,
    days: int,
    out_dir: Path,
) -> None:
    # Convert ccxt-style "ETH/USDT" to Binance style "ETHUSDT"
    symbols = [p.replace("/", "") for p in pairs]

    end_ms = int(time.time() * 1000)
    interval_ms = _interval_to_ms(interval)
    start_ms = end_ms - days * 86_400 * 1000

    logger.info(
        "Fetching %d days of %s klines for: %s",
        days, interval, ", ".join(symbols),
    )

    connector = aiohttp.TCPConnector(limit=4)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = {
            sym: fetch_klines(session, sym, interval, start_ms, end_ms)
            for sym in symbols
        }
        for sym, coro in tasks.items():
            rows = await coro
            fname = f"{sym}_{interval}.csv"
            klines_to_csv(rows, out_dir / fname)


def _interval_to_ms(interval: str) -> int:
    """Return milliseconds for a Binance interval string."""
    mapping = {
        "1m": 60_000,
        "5m": 300_000,
        "15m": 900_000,
        "30m": 1_800_000,
        "1h": 3_600_000,
        "4h": 14_400_000,
        "1d": 86_400_000,
    }
    return mapping.get(interval, 3_600_000)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download Binance OHLCV data")
    parser.add_argument(
        "--pairs",
        nargs="+",
        default=["ETH/USDT", "SOL/USDT", "BTC/USDT"],
        help="Symbols in ccxt format, e.g. ETH/USDT SOL/USDT",
    )
    parser.add_argument(
        "--interval",
        default="1h",
        choices=["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
        help="Kline interval (default: 1h)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="How many days of history to download (default: 90)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("backend/backtest/data"),
        help="Output directory for CSV files",
    )
    args = parser.parse_args()

    asyncio.run(main(args.pairs, args.interval, args.days, args.out))
