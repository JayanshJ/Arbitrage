"""Pair selection and cointegration analysis CLI.

Reads historical close prices from CSV files produced by fetch_data.py and
runs the same 4-step validation used by the live engine:

  1. OLS hedge ratio  (log-price regression)
  2. Engle-Granger cointegration test
  3. ADF stationarity of the spread
  4. Ornstein-Uhlenbeck half-life (must be 2 h – 168 h)

Usage (from repo root):
    # Analyse a specific pair
    python -m backend.backtest.cointegration \
        --a backend/backtest/data/ETHUSDT_1h.csv \
        --b backend/backtest/data/SOLUSDT_1h.csv

    # Scan all candidate pairs in the data/ directory
    python -m backend.backtest.cointegration --scan backend/backtest/data/
"""

from __future__ import annotations

import argparse
import csv
import itertools
import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_closes(csv_path: Path) -> tuple[list[float], list[str]]:
    """Return (close_prices, timestamps) from a kline CSV."""
    closes: list[float] = []
    timestamps: list[str] = []
    with csv_path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            closes.append(float(row["close"]))
            timestamps.append(row["timestamp"])
    return closes, timestamps


# ---------------------------------------------------------------------------
# Validation — mirrors pair_validator.py logic
# ---------------------------------------------------------------------------

def _ols_hedge_ratio(log_a: np.ndarray, log_b: np.ndarray) -> float:
    """OLS: log_a = alpha + beta * log_b  →  return beta."""
    try:
        import statsmodels.api as sm
        X = sm.add_constant(log_b)
        result = sm.OLS(log_a, X).fit()
        return float(result.params[1])
    except ImportError:
        # Fallback: numpy lstsq
        A = np.column_stack([np.ones_like(log_b), log_b])
        coeffs, _, _, _ = np.linalg.lstsq(A, log_a, rcond=None)
        return float(coeffs[1])


def _half_life(spread: np.ndarray) -> float:
    """OU half-life estimate via AR(1) regression on spread differences."""
    try:
        import statsmodels.api as sm
        delta = np.diff(spread)
        lagged = spread[:-1]
        X = sm.add_constant(lagged)
        result = sm.OLS(delta, X).fit()
        kappa = -result.params[1]
        if kappa <= 0:
            return float("inf")
        return float(np.log(2) / kappa)
    except ImportError:
        return float("inf")


def analyse_pair(
    closes_a: list[float],
    closes_b: list[float],
    symbol_a: str,
    symbol_b: str,
    interval_hours: float = 1.0,
    p_threshold: float = 0.05,
    min_half_life_hours: float = 2.0,
    max_half_life_hours: float = 168.0,
) -> dict:
    """
    Run the full 4-step validation and return a results dict.

    Returns
    -------
    dict with keys:
        tradeable, reason,
        hedge_ratio, half_life_hours, p_value_coint, p_value_adf,
        spread_mean, spread_std, n_points
    """
    try:
        from statsmodels.tsa.stattools import coint, adfuller
    except ImportError:
        return {"tradeable": False, "reason": "statsmodels not installed"}

    n = min(len(closes_a), len(closes_b))
    if n < 60:
        return {"tradeable": False, "reason": f"Insufficient data: {n} < 60 points"}

    log_a = np.log(np.array(closes_a[-n:], dtype=float))
    log_b = np.log(np.array(closes_b[-n:], dtype=float))

    # Step 1: hedge ratio
    hedge_ratio = _ols_hedge_ratio(log_a, log_b)

    # Step 2: Engle-Granger cointegration
    _, p_coint, _ = coint(log_a, log_b)

    # Step 3: ADF on spread
    spread = log_a - hedge_ratio * log_b
    adf_result = adfuller(spread, autolag="AIC")
    p_adf = float(adf_result[1])

    # Step 4: OU half-life
    hl_ticks = _half_life(spread)
    hl_hours = hl_ticks * interval_hours

    spread_mean = float(np.mean(spread))
    spread_std = float(np.std(spread))

    # Decision
    if p_coint >= p_threshold:
        tradeable = False
        reason = f"Not cointegrated (p={p_coint:.4f} ≥ {p_threshold})"
    elif p_adf >= p_threshold:
        tradeable = False
        reason = f"Spread not stationary (ADF p={p_adf:.4f} ≥ {p_threshold})"
    elif hl_hours < min_half_life_hours:
        tradeable = False
        reason = f"Half-life too short: {hl_hours:.1f}h < {min_half_life_hours}h"
    elif hl_hours > max_half_life_hours:
        tradeable = False
        reason = f"Half-life too long: {hl_hours:.1f}h > {max_half_life_hours}h"
    else:
        tradeable = True
        reason = "OK"

    return {
        "pair": f"{symbol_a}/{symbol_b}",
        "tradeable": tradeable,
        "reason": reason,
        "hedge_ratio": round(hedge_ratio, 6),
        "half_life_hours": round(hl_hours, 2),
        "p_value_coint": round(float(p_coint), 6),
        "p_value_adf": round(float(p_adf), 6),
        "spread_mean": round(spread_mean, 6),
        "spread_std": round(spread_std, 6),
        "n_points": n,
    }


def _print_result(res: dict) -> None:
    status = "✅ TRADEABLE" if res.get("tradeable") else "❌ SKIP"
    print(f"\n{'─'*60}")
    print(f"  {res.get('pair', '?')}  →  {status}")
    if not res.get("tradeable"):
        print(f"  Reason : {res.get('reason')}")
    else:
        print(f"  Hedge β       : {res['hedge_ratio']}")
        print(f"  Half-life     : {res['half_life_hours']:.1f} h")
        print(f"  Coint p-value : {res['p_value_coint']:.4f}")
        print(f"  ADF   p-value : {res['p_value_adf']:.4f}")
        print(f"  Spread mean   : {res['spread_mean']:.6f}")
        print(f"  Spread std    : {res['spread_std']:.6f}")
        print(f"  Data points   : {res['n_points']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _interval_hours(interval_str: str) -> float:
    mapping = {
        "1m": 1 / 60, "5m": 5 / 60, "15m": 0.25, "30m": 0.5,
        "1h": 1.0, "4h": 4.0, "1d": 24.0,
    }
    return mapping.get(interval_str, 1.0)


def _symbol_from_path(p: Path) -> str:
    """e.g.  ETHUSDT_1h.csv  →  ETHUSDT"""
    return p.stem.split("_")[0]


def _interval_from_path(p: Path) -> str:
    """e.g.  ETHUSDT_1h.csv  →  1h"""
    parts = p.stem.split("_")
    return parts[1] if len(parts) > 1 else "1h"


def cmd_analyse(args: argparse.Namespace) -> None:
    path_a = Path(args.a)
    path_b = Path(args.b)
    closes_a, _ = load_closes(path_a)
    closes_b, _ = load_closes(path_b)
    sym_a = args.sym_a or _symbol_from_path(path_a)
    sym_b = args.sym_b or _symbol_from_path(path_b)
    interval = _interval_from_path(path_a)
    result = analyse_pair(
        closes_a, closes_b, sym_a, sym_b,
        interval_hours=_interval_hours(interval),
    )
    _print_result(result)


def cmd_scan(args: argparse.Namespace) -> None:
    data_dir = Path(args.scan)
    csv_files = sorted(data_dir.glob("*.csv"))
    if len(csv_files) < 2:
        print(f"Need at least 2 CSV files in {data_dir}")
        return

    tradeable = []
    for path_a, path_b in itertools.combinations(csv_files, 2):
        sym_a = _symbol_from_path(path_a)
        sym_b = _symbol_from_path(path_b)
        interval = _interval_from_path(path_a)
        closes_a, _ = load_closes(path_a)
        closes_b, _ = load_closes(path_b)
        result = analyse_pair(
            closes_a, closes_b, sym_a, sym_b,
            interval_hours=_interval_hours(interval),
        )
        _print_result(result)
        if result.get("tradeable"):
            tradeable.append(result)

    print(f"\n{'='*60}")
    print(f"  {len(tradeable)} tradeable pair(s) found out of "
          f"{len(list(itertools.combinations(csv_files, 2)))} candidates.")
    for r in tradeable:
        print(f"    • {r['pair']}  β={r['hedge_ratio']}  "
              f"hl={r['half_life_hours']:.1f}h  "
              f"p={r['p_value_coint']:.4f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pairs cointegration analyser")
    sub = parser.add_subparsers(dest="cmd")

    # Analyse a specific pair
    p_analyse = sub.add_parser("analyse", help="Analyse one pair")
    p_analyse.add_argument("--a", required=True, help="CSV file for symbol A")
    p_analyse.add_argument("--b", required=True, help="CSV file for symbol B")
    p_analyse.add_argument("--sym-a", dest="sym_a", default=None)
    p_analyse.add_argument("--sym-b", dest="sym_b", default=None)

    # Scan all pairs in a directory
    p_scan = sub.add_parser("scan", help="Scan all pairs in a directory")
    p_scan.add_argument("--dir", dest="scan", required=True,
                        help="Directory containing SYMBOL_interval.csv files")

    # Allow top-level --a/--b as shortcut for analyse
    parser.add_argument("--a", default=None, help="CSV file for symbol A")
    parser.add_argument("--b", default=None, help="CSV file for symbol B")
    parser.add_argument("--sym-a", dest="sym_a", default=None)
    parser.add_argument("--sym-b", dest="sym_b", default=None)
    parser.add_argument("--scan", default=None,
                        help="Scan all CSV pairs in this directory")

    args = parser.parse_args()

    if args.scan:
        cmd_scan(args)
    elif args.a and args.b:
        cmd_analyse(args)
    else:
        parser.print_help()
