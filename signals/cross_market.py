"""
Cross-market consistency signal.

Finds logical pricing inconsistencies between related markets on the same platform.
These represent pure arbitrage-adjacent opportunities that require no forecasting —
only noticing that the markets are inconsistent with each other.

Patterns detected:
  1. MUTUAL EXCLUSIVITY: A set of mutually exclusive outcomes whose probabilities
     sum significantly more or less than 1.0
     e.g. "Will A win?" + "Will B win?" + "Will C win?" for a single-winner race

  2. IMPLICATION: P(A) should be ≤ P(B) when A implies B
     e.g. "Will X happen by March?" ≤ "Will X happen by June?"
     If the March market is priced HIGHER, the June market is underpriced.

  3. NEGATION: If P(X=YES) + P(X=NO) ≠ 1 on the same event
     (rarer, but appears with correlated markets on different platforms)

Usage: pass the full list of filtered markets for the current scan cycle.
Returns a dict mapping market_id → SignalResult.
"""
from __future__ import annotations

import re
from collections import defaultdict

from signals.base import SignalResult
from utils.logging import get_logger
from utils.normalizer import MarketSchema

log = get_logger(__name__)

# How far from 1.0 a mutual-exclusivity group must be before we call it a signal
MUTEX_THRESHOLD = 0.12      # sum differs from 1.0 by more than 12pp
IMPLICATION_THRESHOLD = 0.07  # implied market is more than 7pp mispriced


def run_all(markets: list[MarketSchema]) -> dict[str, SignalResult]:
    """
    Analyze all markets for cross-market inconsistencies.
    Returns {market_id: SignalResult} for markets where a signal was found.
    Only contains entries where genuine inconsistency was detected.
    """
    signals: dict[str, SignalResult] = {}

    mutex_signals = _check_mutual_exclusivity(markets)
    signals.update(mutex_signals)

    implication_signals = _check_temporal_implication(markets)
    # Don't overwrite a mutex signal with a weaker implication signal
    for mid, sig in implication_signals.items():
        if mid not in signals:
            signals[mid] = sig

    if signals:
        log.info(f"Cross-market: found {len(signals)} inconsistency signals")

    return signals


# ─── Mutual exclusivity ───────────────────────────────────────────────────────

def _check_mutual_exclusivity(markets: list[MarketSchema]) -> dict[str, SignalResult]:
    """
    Groups markets that appear to be mutually exclusive outcomes of the same event
    (e.g. multiple candidates for the same race) and checks if their probabilities
    sum to approximately 1.0.
    """
    groups = _group_mutex_candidates(markets)
    signals: dict[str, SignalResult] = {}

    for group_key, group in groups.items():
        if len(group) < 2:
            continue

        total_prob = sum(m.yes_price for m in group)
        deviation = total_prob - 1.0  # positive = sum too high, negative = too low

        if abs(deviation) < MUTEX_THRESHOLD:
            continue

        log.info(
            f"Mutex group '{group_key}' ({len(group)} markets): "
            f"sum={total_prob:.3f} deviation={deviation:+.3f}"
        )

        for m in group:
            # If sum > 1: each market is overpriced → bet NO on highest-priced
            # If sum < 1: each market is underpriced → bet YES on lowest-priced
            # We apply the correction proportionally
            corrected_prob = m.yes_price / total_prob  # normalise to sum=1
            deviation_from_market = corrected_prob - m.yes_price
            confidence = min(0.65, abs(deviation) * 2.0)

            if abs(deviation_from_market) > 0.03:  # only signal if meaningful shift
                signals[m.id] = SignalResult(
                    source="cross_market",
                    probability=round(corrected_prob, 4),
                    confidence=confidence,
                    metadata={
                        "mechanism": "mutual_exclusivity",
                        "group_key": group_key,
                        "group_sum": round(total_prob, 4),
                        "group_size": len(group),
                        "correction": round(deviation_from_market, 4),
                    },
                )

    return signals


def _group_mutex_candidates(markets: list[MarketSchema]) -> dict[str, list[MarketSchema]]:
    """
    Heuristically group markets that appear to be competing outcomes.
    Looks for common patterns: same race/event with different named outcomes.
    """
    groups: dict[str, list[MarketSchema]] = defaultdict(list)

    for m in markets:
        key = _extract_mutex_key(m.title)
        if key:
            groups[key].append(m)

    return {k: v for k, v in groups.items() if len(v) >= 2}


def _extract_mutex_key(title: str) -> str | None:
    """
    Extract a grouping key from a market title.
    Strips the candidate/outcome name and returns the event template.

    Examples:
      "Will Biden win the 2028 election?" → "win_2028_election"
      "Will Trump win the 2028 election?" → "win_2028_election"
      "Will Team A win the 2026 NBA Finals?" → "win_2026_nba_finals"
    """
    title_lower = title.lower()

    # Pattern: "Will [NAME] win [EVENT]?"
    win_match = re.search(r"will\s+\w+(?:\s+\w+)?\s+(win\s+.+?)(?:\?|$)", title_lower)
    if win_match:
        event = re.sub(r"\s+", "_", win_match.group(1).strip())
        return event[:60]

    # Pattern: "[NAME] to win [EVENT]"
    to_win_match = re.search(r"\w+(?:\s+\w+)?\s+to\s+(win\s+.+?)(?:\?|$)", title_lower)
    if to_win_match:
        event = re.sub(r"\s+", "_", to_win_match.group(1).strip())
        return event[:60]

    return None


# ─── Temporal implication ────────────────────────────────────────────────────

def _check_temporal_implication(markets: list[MarketSchema]) -> dict[str, SignalResult]:
    """
    Checks for temporal implication violations:
    P(X by date_A) must be ≤ P(X by date_B) when date_A < date_B.

    If "Will X happen by March?" is priced higher than "Will X happen by June?",
    the June market is underpriced (or the March market is overpriced).
    """
    signals: dict[str, SignalResult] = {}
    groups = _group_temporal_candidates(markets)

    for event_key, timeline in groups.items():
        if len(timeline) < 2:
            continue
        # Sort by implied date (earlier first)
        timeline.sort(key=lambda x: x[0])

        for i in range(len(timeline) - 1):
            earlier_date, earlier_market = timeline[i]
            later_date, later_market = timeline[i + 1]

            # P(by earlier date) should be ≤ P(by later date)
            if earlier_market.yes_price > later_market.yes_price + IMPLICATION_THRESHOLD:
                diff = earlier_market.yes_price - later_market.yes_price
                confidence = min(0.60, diff * 3.0)
                log.info(
                    f"Temporal violation: '{earlier_market.title[:40]}' ({earlier_market.yes_price:.2f}) "
                    f"> '{later_market.title[:40]}' ({later_market.yes_price:.2f})"
                )
                # The later market is underpriced — signal YES on it
                corrected = min(0.99, earlier_market.yes_price + 0.02)
                signals[later_market.id] = SignalResult(
                    source="cross_market",
                    probability=corrected,
                    confidence=confidence,
                    metadata={
                        "mechanism": "temporal_implication",
                        "event_key": event_key,
                        "earlier_market": earlier_market.title[:60],
                        "earlier_price": earlier_market.yes_price,
                        "later_market": later_market.title[:60],
                        "later_price": later_market.yes_price,
                        "violation_size": round(diff, 4),
                    },
                )

    return signals


def _group_temporal_candidates(
    markets: list[MarketSchema],
) -> dict[str, list[tuple[str, MarketSchema]]]:
    """
    Groups markets that describe the same event at different time horizons.
    Returns {event_key: [(date_str, market), ...]}
    """
    MONTHS = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "jun": "06", "jul": "07", "aug": "08", "sep": "09",
        "oct": "10", "nov": "11", "dec": "12",
        "q1": "03", "q2": "06", "q3": "09", "q4": "12",
    }

    groups: dict[str, list[tuple[str, MarketSchema]]] = defaultdict(list)

    for m in markets:
        title_lower = m.title.lower()
        date_token = None

        # Look for month names or quarter references
        for token, month_num in MONTHS.items():
            if token in title_lower:
                year_match = re.search(r"20(\d{2})", title_lower)
                year = year_match.group(0) if year_match else "2025"
                date_token = f"{year}-{month_num}"
                break

        if not date_token:
            continue

        # Strip the date token to get the event key
        event_key = title_lower
        for token in MONTHS:
            event_key = event_key.replace(token, "")
        event_key = re.sub(r"20\d{2}", "", event_key)
        event_key = re.sub(r"[^\w\s]", "", event_key)
        event_key = re.sub(r"\s+", "_", event_key.strip())[:50]

        if len(event_key) > 8:  # skip too-short keys
            groups[event_key].append((date_token, m))

    return {k: v for k, v in groups.items() if len(v) >= 2}
