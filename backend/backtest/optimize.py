"""Walk-forward parameter optimizer for the pairs trading strategy.

Splits historical tick data into IN-SAMPLE (train) and OUT-OF-SAMPLE (test)
windows, grid-searches over key parameters on the train window, and validates
the best set on the test window.  Prevents overfitting.

Usage (from repo root):
    # Optimize one pair using collected tick data
    python -m backend.backtest.optimize \\
        --a backend/data/ticks/ticks_2026-03-11.csv \\
        --symbol-a ETH-USD --symbol-b SOL-USD

    # Optimize using pre-downloaded OHLCV data from fetch_data.py
    python -m backend.backtest.optimize \\
        --a backend/backtest/data/ETHUSDT_1h.csv \\
        --b backend/backtest/data/SOLUSDT_1h.csv

    # Tune only entry/exit thresholds, fix everything else
    python -m backend.backtest.optimize \\
        --a ... --b ... \\
        --entry-z 2.0 2.5 3.0 \\
        --exit-z 0.2 0.3 0.5 \\
        --z-window 60
"""

from __future__ import annotations

import argparse
import csv
import itertools
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from .engine import BacktestEngine, Tick, summary, load_ticks

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")


# ---------------------------------------------------------------------------
# Parameter grid
# ---------------------------------------------------------------------------

@dataclass
class ParamSet:
    entry_z: float
    exit_z: float
    z_window: int
    stop_loss_z: float = 4.0
    max_hold_hours: float = 168.0

    def label(self) -> str:
        return (
            f"entry={self.entry_z}  exit={self.exit_z}  "
            f"zwin={self.z_window}  stop={self.stop_loss_z}"
        )


@dataclass
class Result:
    params: ParamSet
    split: str          # "in_sample" | "out_of_sample"
    trades: int
    win_rate: float
    net_pnl: float
    return_pct: float
    max_dd_pct: float
    sharpe: float

    def score(self) -> float:
        """Composite score used for ranking.

        Prioritises Sharpe, penalises drawdown, requires minimum trade count.
        """
        if self.trades < 10:
            return -999.0
        dd_penalty = max(0, self.max_dd_pct - 5) * 0.5
        return self.sharpe - dd_penalty

    def __str__(self) -> str:
        return (
            f"[{self.split:14s}] trades={self.trades:3d}  "
            f"wr={self.win_rate:5.1f}%  "
            f"pnl=${self.net_pnl:+8.2f}  "
            f"ret={self.return_pct:+6.2f}%  "
            f"dd={self.max_dd_pct:5.2f}%  "
            f"sharpe={self.sharpe:6.3f}  "
            f"score={self.score():6.3f}"
        )


# ---------------------------------------------------------------------------
# Tick loading from live recorder CSVs (different format from OHLCV CSVs)
# ---------------------------------------------------------------------------

def load_live_ticks(
    path: Path,
    symbol_a: str,
    symbol_b: str,
) -> list[Tick]:
    """Load ticks from the live recorder CSV (one row per tick per symbol).

    Groups ticks by timestamp-second and takes the last mid_price seen
    for each symbol in that second window.
    """
    prices_a: dict[str, float] = {}
    prices_b: dict[str, float] = {}

    with path.open() as f:
        for row in csv.DictReader(f):
            sym = row["symbol"]
            ts_sec = row["timestamp_utc"][:19]  # truncate to second
            mid = float(row["mid_price"])
            if sym == symbol_a:
                prices_a[ts_sec] = mid
            elif sym == symbol_b:
                prices_b[ts_sec] = mid

    common = sorted(set(prices_a) & set(prices_b))
    if not common:
        raise ValueError(
            f"No overlapping timestamps for {symbol_a}/{symbol_b} in {path}"
        )
    logger.info(
        "Loaded %d aligned tick-seconds from %s", len(common), path.name
    )
    return [Tick(ts, prices_a[ts], prices_b[ts]) for ts in common]


def load_multi_day(
    paths: list[Path],
    symbol_a: str,
    symbol_b: str,
    is_ohlcv: bool = False,
) -> list[Tick]:
    """Concatenate ticks from multiple daily CSV files."""
    all_ticks: list[Tick] = []
    for p in sorted(paths):
        if is_ohlcv:
            # Handled by caller — must provide two files (a and b)
            raise ValueError("Use load_ticks() for OHLCV data")
        all_ticks.extend(load_live_ticks(p, symbol_a, symbol_b))
    return all_ticks


# ---------------------------------------------------------------------------
# Walk-forward engine
# ---------------------------------------------------------------------------

class WalkForwardOptimizer:
    def __init__(
        self,
        ticks: list[Tick],
        symbol_a: str,
        symbol_b: str,
        interval_hours: float = 1.0 / 3600,  # 1 second per tick by default
        train_pct: float = 0.7,
        initial_balance: float = 5_000.0,
    ) -> None:
        self.ticks = ticks
        self.symbol_a = symbol_a
        self.symbol_b = symbol_b
        self.interval_hours = interval_hours
        self.initial_balance = initial_balance

        split_idx = int(len(ticks) * train_pct)
        self.train_ticks = ticks[:split_idx]
        self.test_ticks = ticks[split_idx:]

        logger.info(
            "Train: %d ticks  |  Test: %d ticks  (%.0f%% / %.0f%%)",
            len(self.train_ticks), len(self.test_ticks),
            train_pct * 100, (1 - train_pct) * 100,
        )

    def _run_once(self, ticks: list[Tick], params: ParamSet) -> dict:
        engine = BacktestEngine(
            symbol_a=self.symbol_a,
            symbol_b=self.symbol_b,
            ticks=ticks,
            interval_hours=self.interval_hours,
            initial_balance=self.initial_balance,
            entry_z=params.entry_z,
            exit_z=params.exit_z,
            stop_loss_z=params.stop_loss_z,
            max_hold_hours=params.max_hold_hours,
            z_window=params.z_window,
        )
        engine.run()
        return summary(engine)

    def _make_result(self, params: ParamSet, stats: dict, split: str) -> Result:
        return Result(
            params=params,
            split=split,
            trades=stats["total_trades"],
            win_rate=stats.get("win_rate", 0.0),
            net_pnl=stats.get("total_net_pnl", 0.0),
            return_pct=stats.get("return_pct", 0.0),
            max_dd_pct=stats.get("max_drawdown_pct", 0.0),
            sharpe=stats.get("sharpe", 0.0),
        )

    def run(self, grid: list[ParamSet]) -> tuple[ParamSet, list[Result]]:
        """Grid-search on train, validate best on test.

        Returns (best_params, all_results).
        """
        logger.info("Grid search over %d parameter sets...", len(grid))

        train_results: list[Result] = []
        for i, params in enumerate(grid, 1):
            stats = self._run_once(self.train_ticks, params)
            r = self._make_result(params, stats, "in_sample")
            train_results.append(r)
            if i % 10 == 0 or i == len(grid):
                logger.info("  %d/%d done", i, len(grid))

        train_results.sort(key=lambda r: r.score(), reverse=True)

        # Validate top-5 on out-of-sample
        top5 = train_results[:5]
        oos_results: list[Result] = []
        print(f"\n{'='*70}")
        print("  TOP 5 IN-SAMPLE RESULTS")
        print(f"{'='*70}")
        for rank, r in enumerate(top5, 1):
            print(f"\n  #{rank}  {r.params.label()}")
            print(f"  {r}")

            oos_stats = self._run_once(self.test_ticks, r.params)
            oos = self._make_result(r.params, oos_stats, "out_of_sample")
            oos_results.append(oos)
            print(f"  {oos}")

        # Best = highest out-of-sample score
        best_oos = max(oos_results, key=lambda r: r.score())
        best_params = best_oos.params

        print(f"\n{'='*70}")
        print("  RECOMMENDED PARAMETERS (best out-of-sample score)")
        print(f"{'='*70}")
        print(f"  entry_z_threshold  : {best_params.entry_z}")
        print(f"  exit_z_threshold   : {best_params.exit_z}")
        print(f"  z_window           : {best_params.z_window}")
        print(f"  stop_loss_z        : {best_params.stop_loss_z}")
        print(f"  {best_oos}")
        print()

        return best_params, train_results + oos_results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

DEFAULT_ENTRY_Z   = [2.0, 2.5, 3.0]
DEFAULT_EXIT_Z    = [0.2, 0.3, 0.5]
DEFAULT_Z_WINDOWS = [40, 60, 90]
DEFAULT_STOP_Z    = [4.0]


def build_grid(args: argparse.Namespace) -> list[ParamSet]:
    entry_zs   = args.entry_z or DEFAULT_ENTRY_Z
    exit_zs    = args.exit_z or DEFAULT_EXIT_Z
    z_windows  = args.z_window or DEFAULT_Z_WINDOWS
    stop_zs    = args.stop_z or DEFAULT_STOP_Z

    grid = [
        ParamSet(ez, xz, zw, sz)
        for ez, xz, zw, sz in itertools.product(
            entry_zs, exit_zs, z_windows, stop_zs
        )
        if xz < ez  # exit threshold must be less than entry
    ]
    return grid


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Walk-forward parameter optimizer")

    # Data sources — two modes:
    #   Mode 1: two OHLCV CSVs (--a and --b, one per symbol)
    #   Mode 2: one live-recorder CSV (--ticks) with --symbol-a / --symbol-b
    parser.add_argument("--a", default=None,
                        help="OHLCV CSV for symbol A (from fetch_data.py)")
    parser.add_argument("--b", default=None,
                        help="OHLCV CSV for symbol B (from fetch_data.py)")
    parser.add_argument("--ticks", nargs="+", default=None,
                        help="Live recorder CSV(s) (ticks_YYYY-MM-DD.csv)")
    parser.add_argument("--symbol-a", dest="symbol_a", default="ETH-USD")
    parser.add_argument("--symbol-b", dest="symbol_b", default="SOL-USD")

    # Grid parameters (can supply multiple values per flag)
    parser.add_argument("--entry-z", dest="entry_z", nargs="+",
                        type=float, default=None,
                        help=f"Entry z thresholds (default: {DEFAULT_ENTRY_Z})")
    parser.add_argument("--exit-z", dest="exit_z", nargs="+",
                        type=float, default=None,
                        help=f"Exit z thresholds (default: {DEFAULT_EXIT_Z})")
    parser.add_argument("--z-window", dest="z_window", nargs="+",
                        type=int, default=None,
                        help=f"Z-score windows in ticks (default: {DEFAULT_Z_WINDOWS})")
    parser.add_argument("--stop-z", dest="stop_z", nargs="+",
                        type=float, default=None,
                        help=f"Stop-loss z levels (default: {DEFAULT_STOP_Z})")

    # Walk-forward split
    parser.add_argument("--train-pct", type=float, default=0.7,
                        help="Fraction of data used for training (default: 0.7)")
    parser.add_argument("--balance", type=float, default=5_000.0)
    parser.add_argument("--interval-hours", type=float, default=None,
                        help="Hours per tick. Auto-detected for OHLCV files.")

    args = parser.parse_args()

    # Load ticks
    if args.ticks:
        tick_paths = [Path(p) for p in args.ticks]
        ticks = load_multi_day(tick_paths, args.symbol_a, args.symbol_b)
        interval_h = args.interval_hours or (1 / 3600)  # ~1 second
    elif args.a and args.b:
        ticks = load_ticks(Path(args.a), Path(args.b))
        # Auto-detect from filename (e.g. ETHUSDT_1h.csv → 1h)
        stem = Path(args.a).stem.split("_")
        iv_map = {"1m": 1/60, "5m": 5/60, "15m": 0.25, "30m": 0.5,
                  "1h": 1.0, "4h": 4.0, "1d": 24.0}
        interval_h = args.interval_hours or iv_map.get(stem[-1] if len(stem)>1 else "1h", 1.0)
    else:
        parser.error("Provide either --ticks <csv(s)> or both --a and --b")

    if len(ticks) < 500:
        logger.error("Need at least 500 aligned ticks — got %d. Collect more data.", len(ticks))
        raise SystemExit(1)

    sym_a = args.symbol_a
    sym_b = args.symbol_b

    grid = build_grid(args)
    logger.info(
        "Pair: %s/%s  |  Ticks: %d  |  Grid size: %d  |  Interval: %.4f h",
        sym_a, sym_b, len(ticks), len(grid), interval_h,
    )

    opt = WalkForwardOptimizer(
        ticks=ticks,
        symbol_a=sym_a,
        symbol_b=sym_b,
        interval_hours=interval_h,
        train_pct=args.train_pct,
        initial_balance=args.balance,
    )

    best, _ = opt.run(grid)

    print("\nPaste these into backend/config/risk.json to deploy:")
    print(f'  "entry_z_threshold": {best.entry_z},')
    print(f'  "exit_z_threshold":  {best.exit_z},')
    print(f'  "z_window":          {best.z_window},')
    print(f'  "stop_loss_z":       {best.stop_loss_z}')
