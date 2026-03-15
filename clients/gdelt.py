"""
GDELT 2.0 DOC API client — free, no auth required.
Returns news volume and sentiment tone for a query over a time window.
Used as a lightweight directional signal.
"""
from __future__ import annotations

from typing import Optional

import requests

from utils.logging import get_logger
from utils.retry import with_retry

log = get_logger(__name__)
DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"


class GDELTClient:
    @with_retry()
    def query(self, keywords: str, days_back: int = 3) -> dict:
        """
        Returns dict: {article_count, avg_tone, tone_positive_ratio}
        avg_tone: GDELT tone score, typically -10 to +10
                  negative = more negative coverage, positive = more positive
        Returns empty dict on failure.
        """
        try:
            resp = requests.get(
                DOC_API,
                params={
                    "query": keywords,
                    "mode": "ArtList",
                    "maxrecords": 50,
                    "timespan": f"{days_back}d",
                    "format": "json",
                    "sort": "DateDesc",
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            articles = data.get("articles", [])
            if not articles:
                return {"article_count": 0, "avg_tone": 0.0, "tone_positive_ratio": 0.5}

            tones = []
            positive = 0
            for a in articles:
                tone_str = a.get("tone", "0")
                try:
                    tone_val = float(str(tone_str).split(",")[0])
                    tones.append(tone_val)
                    if tone_val > 0:
                        positive += 1
                except (ValueError, IndexError):
                    pass

            if not tones:
                return {"article_count": len(articles), "avg_tone": 0.0, "tone_positive_ratio": 0.5}

            return {
                "article_count": len(articles),
                "avg_tone": sum(tones) / len(tones),
                "tone_positive_ratio": positive / len(tones),
            }
        except Exception as exc:
            log.debug(f"GDELT query failed: {exc}")
            return {}

    def tone_to_probability_shift(self, gdelt_result: dict, market_price: float) -> float:
        """
        Map GDELT tone to a probability value.
        Shifts market price by up to ±10% based on sentiment.
        avg_tone range is roughly -10 to +10; we normalise to -1 to +1.
        """
        if not gdelt_result or gdelt_result.get("article_count", 0) == 0:
            return market_price
        tone = gdelt_result.get("avg_tone", 0.0)
        normalised = max(-1.0, min(1.0, tone / 10.0))
        shift = normalised * 0.10
        return max(0.02, min(0.98, market_price + shift))
