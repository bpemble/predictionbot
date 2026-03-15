"""
Metaculus API client — free, no auth required.
Used to cross-reference prediction market questions against Metaculus forecasts.
"""
from __future__ import annotations

from typing import Optional

import requests

from utils.logging import get_logger
from utils.retry import with_retry

log = get_logger(__name__)
BASE = "https://www.metaculus.com/api2"


class MetaculusClient:
    @with_retry()
    def search_questions(self, query: str, limit: int = 5) -> list[dict]:
        resp = requests.get(
            f"{BASE}/questions/",
            params={
                "search": query,
                "limit": limit,
                "order_by": "-votes",
                "status": "open",
                "type": "forecast",
            },
            timeout=20,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        return resp.json().get("results", [])

    def get_best_match_probability(self, market_title: str) -> Optional[float]:
        """
        Searches Metaculus for the closest question and returns the community
        median probability, or None if no good match found.
        """
        # Use shortened keywords for better search results
        keywords = _extract_keywords(market_title)
        try:
            results = self.search_questions(keywords, limit=5)
            if not results:
                return None
            # Use the first result (highest votes / most relevant)
            q = results[0]
            community = q.get("community_prediction", {})
            if isinstance(community, dict):
                pred = community.get("full", {})
                if isinstance(pred, dict):
                    val = pred.get("q2")  # median
                    if val is not None:
                        return float(val)
            # Fallback: resolution_criteria probability
            return None
        except Exception as exc:
            log.debug(f"Metaculus lookup failed for '{market_title[:50]}': {exc}")
            return None


def _extract_keywords(title: str, max_words: int = 6) -> str:
    """Extract the most meaningful keywords from a market title for search."""
    stop_words = {"will", "the", "a", "an", "in", "on", "at", "to", "of", "be",
                  "is", "are", "was", "were", "by", "for", "with", "or", "and"}
    words = [w.strip("?.,!") for w in title.split()]
    keywords = [w for w in words if w.lower() not in stop_words and len(w) > 2]
    return " ".join(keywords[:max_words])
