"""
Signal aggregator: combines multiple SignalResults into a single probability estimate.

Algorithm:
  1. Convert each probability to logit space (avoids linear averaging near 0/1).
  2. Weight by stored Brier-score-derived weights × self-reported confidence.
  3. Apply Bayesian shrinkage toward market price proportional to uncertainty.
  4. Back-transform to probability.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from config import constants
from db import repository
from signals.base import SignalResult
from utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class AggregationResult:
    aggregated_prob: float
    market_implied_prob: float
    edge: float                  # signed: positive = YES is underpriced
    side: str                    # 'yes' or 'no'
    abs_edge: float
    signal_count: int
    signal_breakdown: dict       # {source: probability}


def _logit(p: float) -> float:
    p = max(0.001, min(0.999, p))
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def aggregate(
    signals: list[SignalResult],
    market_price: float,
    weights: Optional[dict[str, float]] = None,
) -> Optional[AggregationResult]:
    """
    Returns AggregationResult, or None if fewer than MIN_SIGNALS_REQUIRED valid signals.
    """
    if weights is None:
        weights = repository.get_signal_weights()

    # Filter valid signals
    valid = [s for s in signals if s.is_valid() and
             s.confidence >= constants.MIN_SIGNAL_CONFIDENCE]

    if len(valid) < constants.MIN_SIGNALS_REQUIRED:
        log.debug(f"Aggregator: only {len(valid)} valid signals (need {constants.MIN_SIGNALS_REQUIRED})")
        return None

    # Step 1 + 2: Weighted logit average
    logit_numerator = 0.0
    weight_sum = 0.0

    for sig in valid:
        base_weight = weights.get(sig.source, 0.1)
        # Modulate by confidence: 70% base + 30% confidence
        eff_weight = base_weight * (
            (1 - constants.CONFIDENCE_WEIGHT_ALPHA) +
            constants.CONFIDENCE_WEIGHT_ALPHA * sig.confidence
        )
        logit_numerator += eff_weight * _logit(sig.probability)
        weight_sum += eff_weight

    if weight_sum == 0:
        return None

    logit_agg = logit_numerator / weight_sum

    # Step 3: Bayesian shrinkage toward market price
    avg_confidence = sum(s.confidence for s in valid) / len(valid)
    shrinkage = 1.0 - (0.4 * (1.0 - avg_confidence))
    logit_market = _logit(market_price)
    logit_final = shrinkage * logit_agg + (1 - shrinkage) * logit_market

    # Step 4: Back-transform
    aggregated_prob = _sigmoid(logit_final)

    # Step 5: Edge and direction
    edge = aggregated_prob - market_price
    side = "yes" if edge >= 0 else "no"
    abs_edge = abs(edge)

    breakdown = {s.source: round(s.probability, 3) for s in valid}

    log.info(
        f"Aggregated: p={aggregated_prob:.3f} market={market_price:.3f} "
        f"edge={edge:+.3f} side={side} signals={len(valid)}"
    )

    return AggregationResult(
        aggregated_prob=round(aggregated_prob, 4),
        market_implied_prob=market_price,
        edge=round(edge, 4),
        side=side,
        abs_edge=round(abs_edge, 4),
        signal_count=len(valid),
        signal_breakdown=breakdown,
    )
