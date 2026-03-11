"""Live-money readiness report.

Runs the full analysis pipeline on your collected data and prints a
PASS / FAIL checklist for every criterion that must be met before you
put real money on the line.

Usage:
    python -m backend.backtest.report \\
        --a backend/backtest/data/ETHUSDT_1h.csv \\
        --b backend/backtest/data/SOLUSDT_1h.csv \\
        --capital 5000

    # Or with live-recorded ticks:
    python -m backend.backtest.report \\
        --ticks backend/data/ticks/ticks_*.csv \\
        --symbol-a ETH-USD --symbol-b SOL-USD \\
        --capital 5000
"""

from __future__ import annotations

import argparse
import csv
import logging
from pathlib import Path
from typing import Optional

import numpy as np

from .cointegration import analyse_pair
from .engine import BacktestEngine, Tick, summary, load_ticks
from .optimize import load_multi_day

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)  # suppress noisy backtest logs


# ---------------------------------------------------------------------------
# Gate criteria — edit these if your risk tolerance differs
# ---------------------------------------------------------------------------

GATES = {
    # Statistical validation
    "cointegration_p":    ("Cointegration p-value",        "< 0.05",  lambda v: v < 0.05),
    "adf_p":              ("Spread ADF p-value",            "< 0.05",  lambda v: v < 0.05),
    "half_life_min":      ("Half-life ≥ 2 h",               "≥ 2 h",   lambda v: v >= 2.0),
    "half_life_max":      ("Half-life ≤ 7 days",            "≤ 168 h", lambda v: v <= 168.0),

    # Backtest performance (in-sample)
    "min_trades_is":      ("Min trades in-sample",          "≥ 30",    lambda v: v >= 30),
    "win_rate_is":        ("Win rate in-sample",            "≥ 55 %",  lambda v: v >= 55.0),
    "sharpe_is":          ("Sharpe in-sample (annualised)", "≥ 1.0",   lambda v: v >= 1.0),
    "max_dd_is":          ("Max drawdown in-sample",        "≤ 15 %",  lambda v: v <= 15.0),

    # Out-of-sample validation (most important — proves no overfitting)
    "min_trades_oos":     ("Min trades out-of-sample",      "≥ 10",    lambda v: v >= 10),
    "win_rate_oos":       ("Win rate out-of-sample",        "≥ 50 %",  lambda v: v >= 50.0),
    "sharpe_oos":         ("Sharpe out-of-sample",          "≥ 0.5",   lambda v: v >= 0.5),
    "max_dd_oos":         ("Max drawdown out-of-sample",    "≤ 20 %",  lambda v: v <= 20.0),
    "positive_return_oos":("Positive return out-of-sample", "> 0 %",   lambda v: v > 0.0),

    # Cost sanity
    "avg_trade_positive": ("Avg net P&L per trade",         "> $0",    lambda v: v > 0.0),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _interval_hours(path: Path) -> float:
    iv_map = {"1m": 1/60, "5m": 5/60, "15m": 0.25, "30m": 0.5,
              "1h": 1.0, "4h": 4.0, "1d": 24.0}
    stem = path.stem.split("_")
    return iv_map.get(stem[-1] if len(stem) > 1 else "1h", 1.0)


def _prices(ticks: list[Tick]) -> tuple[list[float], list[float]]:
    return [t.price_a for t in ticks], [t.price_b for t in ticks]


def _run_backtest(
    ticks: list[Tick],
    symbol_a: str,
    symbol_b: str,
    interval_h: float,
    balance: float,
) -> dict:
    engine = BacktestEngine(
        symbol_a=symbol_a,
        symbol_b=symbol_b,
        ticks=ticks,
        interval_hours=interval_h,
        initial_balance=balance,
    )
    engine.run()
    return summary(engine)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def run_report(
    ticks: list[Tick],
    symbol_a: str,
    symbol_b: str,
    interval_h: float,
    capital: float,
    train_pct: float = 0.7,
) -> bool:
    """Run all checks and print the readiness report.  Returns True if all pass."""

    split = int(len(ticks) * train_pct)
    train = ticks[:split]
    test = ticks[split:]

    prices_a_all, prices_b_all = _prices(ticks)

    # --- 1. Cointegration on full dataset ---
    coint = analyse_pair(
        prices_a_all, prices_b_all,
        symbol_a, symbol_b,
        interval_hours=interval_h,
    )

    # --- 2. Backtest in-sample ---
    is_stats = _run_backtest(train, symbol_a, symbol_b, interval_h, capital)

    # --- 3. Backtest out-of-sample ---
    oos_stats = _run_backtest(test, symbol_a, symbol_b, interval_h, capital)

    # --- Collect values ---
    avg_trade = (
        is_stats["total_net_pnl"] / is_stats["total_trades"]
        if is_stats["total_trades"] > 0 else -1.0
    )

    values = {
        "cointegration_p":     coint.get("p_value_coint", 1.0),
        "adf_p":               coint.get("p_value_adf", 1.0),
        "half_life_min":       coint.get("half_life_hours", 0.0),
        "half_life_max":       coint.get("half_life_hours", float("inf")),
        "min_trades_is":       float(is_stats["total_trades"]),
        "win_rate_is":         is_stats.get("win_rate", 0.0),
        "sharpe_is":           is_stats.get("sharpe", 0.0),
        "max_dd_is":           is_stats.get("max_drawdown_pct", 100.0),
        "min_trades_oos":      float(oos_stats["total_trades"]),
        "win_rate_oos":        oos_stats.get("win_rate", 0.0),
        "sharpe_oos":          oos_stats.get("sharpe", 0.0),
        "max_dd_oos":          oos_stats.get("max_drawdown_pct", 100.0),
        "positive_return_oos": oos_stats.get("return_pct", -1.0),
        "avg_trade_positive":  avg_trade,
    }

    # --- Print report ---
    pair = f"{symbol_a} / {symbol_b}"
    print(f"\n{'='*68}")
    print(f"  LIVE-MONEY READINESS REPORT  —  {pair}")
    print(f"  Capital: ${capital:,.0f}  |  "
          f"Data: {len(ticks):,} ticks  |  "
          f"Train: {split:,}  Test: {len(test):,}")
    print(f"{'='*68}\n")

    # Cointegration section
    print("  ── Statistical Validation ──────────────────────────────────")
    print(f"  Hedge ratio   β = {coint.get('hedge_ratio', 'N/A')}")
    print(f"  Half-life       = {coint.get('half_life_hours', 'N/A'):.1f} h" if isinstance(coint.get('half_life_hours'), float) else "  Half-life       = N/A")
    print(f"  Coint p-value   = {coint.get('p_value_coint', 'N/A')}")
    print(f"  ADF   p-value   = {coint.get('p_value_adf', 'N/A')}")
    print()

    # In-sample summary
    print("  ── In-Sample Performance (training data) ───────────────────")
    print(f"  Trades: {is_stats['total_trades']}  "
          f"Win rate: {is_stats.get('win_rate', 0):.1f}%  "
          f"Net P&L: ${is_stats.get('total_net_pnl', 0):+.2f}  "
          f"Return: {is_stats.get('return_pct', 0):+.2f}%")
    print(f"  Max DD: {is_stats.get('max_drawdown_pct', 0):.2f}%  "
          f"Sharpe: {is_stats.get('sharpe', 0):.3f}  "
          f"Avg hold: {is_stats.get('avg_hold_hours', 0):.1f} h")
    print()

    # Out-of-sample summary
    print("  ── Out-of-Sample Performance (unseen test data) ────────────")
    print(f"  Trades: {oos_stats['total_trades']}  "
          f"Win rate: {oos_stats.get('win_rate', 0):.1f}%  "
          f"Net P&L: ${oos_stats.get('total_net_pnl', 0):+.2f}  "
          f"Return: {oos_stats.get('return_pct', 0):+.2f}%")
    print(f"  Max DD: {oos_stats.get('max_drawdown_pct', 0):.2f}%  "
          f"Sharpe: {oos_stats.get('sharpe', 0):.3f}  "
          f"Avg hold: {oos_stats.get('avg_hold_hours', 0):.1f} h")
    print()

    # Gate checklist
    print("  ── Readiness Checklist ─────────────────────────────────────")
    all_pass = True
    for key, (label, threshold, check_fn) in GATES.items():
        val = values.get(key, None)
        if val is None:
            status = "⚠️  N/A"
        else:
            passed = check_fn(val)
            if not passed:
                all_pass = False
            status = "✅ PASS" if passed else "❌ FAIL"
            # format value nicely
            if "pct" in key or "rate" in key or "return" in key:
                val_str = f"{val:.2f}%"
            elif "trades" in key:
                val_str = str(int(val))
            elif "sharpe" in key:
                val_str = f"{val:.3f}"
            elif "_p" in key:
                val_str = f"{val:.4f}"
            elif "hours" in key or "life" in key:
                val_str = f"{val:.1f} h"
            else:
                val_str = f"${val:.2f}" if "trade" in key else f"{val:.3f}"
            status = f"{status}  ({val_str})"
        print(f"  {status:<40}  {label}  [{threshold}]")

    print(f"\n{'='*68}")
    if all_pass:
        print("  🟢  ALL CHECKS PASSED — you may deploy with real money.")
        print(f"  Suggested starting capital: ${capital:,.0f}")
        print("  Start SMALL (10–20% of your intended capital) and scale up")
        print("  only after 2+ weeks of live paper trading confirms the numbers.")
    else:
        print("  🔴  SOME CHECKS FAILED — DO NOT use real money yet.")
        print("  Fix the failing criteria first:")
        print("    • Collect more data (aim for 30+ days)")
        print("    • Re-run optimize.py to find better parameters")
        print("    • Consider a different pair if cointegration fails")
    print(f"{'='*68}\n")

    return all_pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live-money readiness report")
    parser.add_argument("--a", default=None, help="OHLCV CSV for symbol A")
    parser.add_argument("--b", default=None, help="OHLCV CSV for symbol B")
    parser.add_argument("--ticks", nargs="+", default=None,
                        help="Live recorder CSV(s)")
    parser.add_argument("--symbol-a", dest="symbol_a", default="ETH-USD")
    parser.add_argument("--symbol-b", dest="symbol_b", default="SOL-USD")
    parser.add_argument("--capital", type=float, default=5_000.0,
                        help="Intended real-money capital (default: $5000)")
    parser.add_argument("--train-pct", type=float, default=0.7)
    parser.add_argument("--interval-hours", type=float, default=None)
    args = parser.parse_args()

    if args.ticks:
        tick_paths = [Path(p) for p in args.ticks]
        ticks = load_multi_day(tick_paths, args.symbol_a, args.symbol_b)
        interval_h = args.interval_hours or (1 / 3600)
    elif args.a and args.b:
        ticks = load_ticks(Path(args.a), Path(args.b))
        interval_h = args.interval_hours or _interval_hours(Path(args.a))
    else:
        parser.error("Provide either --ticks <csv(s)> or both --a and --b")

    passed = run_report(
        ticks=ticks,
        symbol_a=args.symbol_a,
        symbol_b=args.symbol_b,
        interval_h=interval_h,
        capital=args.capital,
        train_pct=args.train_pct,
    )

    raise SystemExit(0 if passed else 1)
