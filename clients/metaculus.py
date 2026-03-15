"""
Metaculus API client.
Requires a free API token: https://www.metaculus.com/accounts/profile/
Set METACULUS_API_KEY in .env.
"""
from __future__ import annotations

from typing import Optional

import requests

from config.settings import get_settings
from utils.logging import get_logger

log = get_logger(__name__)
BASE = "https://www.metaculus.com/api2"


class MetaculusClient:
    def _headers(self) -> dict:
        key = get_settings().metaculus_api_key
        h = {"Accept": "application/json", "User-Agent": "prediction-bot/1.0"}
        if key:
            h["Authorization"] = f"Token {key}"
        return h

    def search_questions(self, query: str, limit: int = 5) -> list[dict]:
        try:
            resp = requests.get(
                f"{BASE}/questions/",
                params={
                    "search": query,
                    "limit": limit,
                    "order_by": "-votes",
                    "status": "open",
                    "type": "forecast",
                },
                timeout=10,
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json().get("results", [])
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in (401, 403):
                log.debug("Metaculus auth failed — check METACULUS_API_KEY in .env")
            elif status == 429:
                log.debug("Metaculus rate-limited — skipping")
            else:
                log.debug(f"Metaculus HTTP {status}: {exc}")
            return []
        except Exception as exc:
            log.debug(f"Metaculus request failed: {exc}")
            return []

    def get_best_match_probability(self, market_title: str) -> Optional[float]:
        """
        Searches Metaculus for the closest question and returns the community
        median probability, or None if no good match found.
        """
        keywords = _extract_keywords(market_title)
        try:
            results = self.search_questions(keywords, limit=3)
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
