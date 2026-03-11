"""Pair cointegration validation.

Determines whether a pair is statistically suitable for pairs trading by
running three sequential tests:

  1. OLS regression  → hedge ratio β (spread = A − β·B)
  2. Engle-Granger cointegration test  → p < 0.05
  3. Augmented Dickey-Fuller on the spread  → stationarity
  4. Ornstein-Uhlenbeck half-life  → 2 h < t½ < 168 h (7 days)

The hedge ratio β is used by the engine for position sizing (buy 1 dollar of A,
short β dollars of B) instead of the naive equal-dollar approach.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    from statsmodels.regression.linear_model import OLS
    from statsmodels.tools import add_constant
    from statsmodels.tsa.stattools import adfuller, coint

    _STATSMODELS = True
except ImportError:
    _STATSMODELS = False
    logger.warning(
        "statsmodels not installed — cointegration testing disabled. "
        "Install with: pip install statsmodels"
    )


@dataclass
class ValidationResult:
    """Result of a full cointegration + OU validation run."""

    is_valid: bool
    reason: str
    p_value: float = 1.0
    hedge_ratio: float = 1.0          # β: spread = A − β·B
    half_life_hours: float = float("inf")
    spread_mean: float = 0.0
    spread_std: float = 0.0
    data_points: int = 0


def validate_pair(
    prices_a: np.ndarray,
    prices_b: np.ndarray,
    tick_interval_seconds: float = 0.5,   # how often prices_a/b are sampled
    min_half_life_hours: float = 2.0,
    max_half_life_hours: float = 168.0,   # 7 days
    p_threshold: float = 0.05,
) -> ValidationResult:
    """Run full cointegration + OU test on a pair.

    Args:
        prices_a / prices_b : Aligned price series (same length).
        tick_interval_seconds: Sampling interval used to convert the OU
            half-life from "ticks" to hours.  The live engine updates every
            0.5 s, so the default matches.
        min/max_half_life_hours: Acceptable mean-reversion window.
        p_threshold: Maximum acceptable cointegration p-value.

    Returns:
        ValidationResult.  is_valid=True means the pair is tradeable.
    """
    n = len(prices_a)

    if not _STATSMODELS:
        # Graceful degradation: skip validation, use simple beta
        beta = float(np.mean(prices_a) / np.mean(prices_b))
        spread = prices_a - beta * prices_b
        return ValidationResult(
            is_valid=True,
            reason="statsmodels unavailable (no cointegration test)",
            hedge_ratio=beta,
            spread_mean=float(spread.mean()),
            spread_std=float(spread.std()),
            data_points=n,
        )

    if n < 60:
        return ValidationResult(
            is_valid=False,
            reason=f"Not enough data: {n} < 60 samples required",
            data_points=n,
        )

    # ------------------------------------------------------------------ #
    # Step 1 — OLS hedge ratio  (spread = A − β·B)                       #
    # ------------------------------------------------------------------ #
    try:
        ols = OLS(prices_a, add_constant(prices_b)).fit()
        beta = float(ols.params[1])
    except Exception as exc:
        return ValidationResult(
            is_valid=False, reason=f"OLS failed: {exc}", data_points=n
        )

    if beta <= 0:
        return ValidationResult(
            is_valid=False,
            reason=f"Negative hedge ratio β={beta:.3f} — pair moves inversely",
            data_points=n,
        )

    spread = prices_a - beta * prices_b

    # ------------------------------------------------------------------ #
    # Step 2 — Engle-Granger cointegration test                           #
    # ------------------------------------------------------------------ #
    try:
        _, p_coint, _ = coint(prices_a, prices_b)
    except Exception as exc:
        return ValidationResult(
            is_valid=False, reason=f"Cointegration test failed: {exc}", data_points=n
        )

    if p_coint >= p_threshold:
        return ValidationResult(
            is_valid=False,
            reason=f"Not cointegrated: p={p_coint:.3f} ≥ {p_threshold}",
            p_value=p_coint,
            hedge_ratio=beta,
            data_points=n,
        )

    # ------------------------------------------------------------------ #
    # Step 3 — ADF stationarity on the spread                             #
    # ------------------------------------------------------------------ #
    try:
        _, p_adf, *_ = adfuller(spread)
    except Exception as exc:
        return ValidationResult(
            is_valid=False,
            reason=f"ADF test failed: {exc}",
            p_value=p_coint,
            hedge_ratio=beta,
            data_points=n,
        )

    if p_adf >= p_threshold:
        return ValidationResult(
            is_valid=False,
            reason=f"Spread not stationary: ADF p={p_adf:.3f}",
            p_value=p_coint,
            hedge_ratio=beta,
            data_points=n,
        )

    # ------------------------------------------------------------------ #
    # Step 4 — Ornstein-Uhlenbeck half-life                               #
    # dS[t] = κ(μ − S[t-1]) + ε   →   half_life = ln(2) / κ             #
    # ------------------------------------------------------------------ #
    try:
        lag = spread[:-1]
        delta = np.diff(spread)
        ou = OLS(delta, add_constant(lag)).fit()
        kappa = -float(ou.params[1])
        if kappa <= 1e-8:
            return ValidationResult(
                is_valid=False,
                reason=f"No mean reversion: κ={kappa:.6f}",
                p_value=p_coint,
                hedge_ratio=beta,
                data_points=n,
            )
        # Convert from ticks to hours
        half_life_ticks = math.log(2) / kappa
        half_life_hours = half_life_ticks * tick_interval_seconds / 3600
    except Exception as exc:
        return ValidationResult(
            is_valid=False,
            reason=f"OU fitting failed: {exc}",
            p_value=p_coint,
            hedge_ratio=beta,
            data_points=n,
        )

    if not (min_half_life_hours <= half_life_hours <= max_half_life_hours):
        return ValidationResult(
            is_valid=False,
            reason=(
                f"Half-life {half_life_hours:.1f} h outside "
                f"[{min_half_life_hours}, {max_half_life_hours}] h window"
            ),
            p_value=p_coint,
            hedge_ratio=beta,
            half_life_hours=half_life_hours,
            data_points=n,
        )

    return ValidationResult(
        is_valid=True,
        reason="OK",
        p_value=p_coint,
        hedge_ratio=beta,
        half_life_hours=half_life_hours,
        spread_mean=float(spread.mean()),
        spread_std=float(spread.std()),
        data_points=n,
    )
