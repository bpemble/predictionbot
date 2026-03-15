"""
Self-learning loop: updates signal weights based on historical Brier scores.

Algorithm:
  1. For each signal source, fetch its most recent BRIER_WINDOW resolved predictions.
  2. Compute mean Brier score (lower = better).
  3. Convert to accuracy = 1 - mean_brier.
  4. Apply EMA update against the current weight.
  5. Normalise all weights to sum = 1.
  6. Persist to signal_weights table.
"""
from __future__ import annotations

from config import constants
from db import repository
from utils.logging import get_logger

log = get_logger(__name__)

SIGNAL_SOURCES = ["llm", "news", "research", "metaculus", "gdelt"]


def calibrate() -> dict[str, float]:
    """
    Run calibration. Returns the new weight dict.
    Requires at least 5 resolved predictions per source to update that source.
    """
    current_weights = repository.get_signal_weights()
    new_weights = dict(current_weights)  # start from current

    updates: list[str] = []

    for source in SIGNAL_SOURCES:
        runs = repository.get_resolved_signal_runs_for_calibration(
            source, constants.BRIER_WINDOW
        )
        if len(runs) < 5:
            log.debug(f"Calibration: skipping {source} — only {len(runs)} resolved predictions")
            continue

        brier_scores = []
        for r in runs:
            prob = float(r["raw_probability"])
            outcome_str = r["outcome"]
            outcome = 1.0 if outcome_str == "yes" else 0.0
            brier_scores.append((prob - outcome) ** 2)

        avg_brier = sum(brier_scores) / len(brier_scores)
        accuracy = 1.0 - avg_brier

        # EMA update: blend new accuracy reading with current weight
        old_weight = current_weights.get(source, 0.1)
        new_weight_raw = constants.EMA_ALPHA * accuracy + (1 - constants.EMA_ALPHA) * old_weight
        new_weights[source] = max(0.01, new_weight_raw)  # floor at 1%

        repository.update_signal_weight(source, new_weights[source], avg_brier, len(runs))
        updates.append(f"{source}: {old_weight:.3f} → {new_weights[source]:.3f} (brier={avg_brier:.4f})")

    # Normalise to sum = 1
    total = sum(new_weights.values())
    if total > 0:
        for source in new_weights:
            new_weights[source] = round(new_weights[source] / total, 4)

    if updates:
        log.info("Signal weight calibration complete:\n  " + "\n  ".join(updates))
        log.info(f"New normalised weights: {new_weights}")
    else:
        log.info("Calibration: insufficient resolved data — weights unchanged")

    return new_weights
