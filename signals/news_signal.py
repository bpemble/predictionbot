"""
News sentiment signal: derives a probability from headline sentiment
without calling the LLM (avoids double-billing Claude).
Uses a simple valence keyword approach; crude but fast and cheap.
"""
from __future__ import annotations

from clients.exa import ExaClient
from clients.newsapi import NewsAPIClient
from signals.base import SignalResult
from utils.logging import get_logger
from utils.normalizer import MarketSchema

log = get_logger(__name__)

_news = NewsAPIClient()
_exa = ExaClient()

# Simple positive/negative keyword lists for prediction-market-relevant language
_POS = {"yes", "confirmed", "approved", "wins", "won", "rises", "beats",
        "exceeds", "passes", "signed", "achieved", "successful", "above",
        "higher", "increase", "increased", "up", "growth", "breakthrough"}
_NEG = {"no", "rejected", "denied", "loses", "lost", "falls", "misses",
        "fails", "blocked", "vetoed", "declined", "below", "lower",
        "decrease", "decreased", "down", "collapse", "disappointing"}


def _sentiment_score(text: str) -> float:
    """Returns score in [-1, +1]. 0 = neutral."""
    words = text.lower().split()
    pos = sum(1 for w in words if w.strip(".,!?;:") in _POS)
    neg = sum(1 for w in words if w.strip(".,!?;:") in _NEG)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / total


def run(market: MarketSchema) -> SignalResult:
    """Generate a news-sentiment-based probability estimate."""
    texts = []
    article_count = 0

    # NewsAPI calls are reserved for the LLM signal (which has richer context).
    # This signal uses Exa only to avoid double-counting the daily API quota.
    if _exa.available():
        exa_results = _exa.search(market.title, num_results=4, days_back=5)
        for r in exa_results:
            texts.append(f"{r['title']} {r['text']}")
        article_count += len(exa_results)

    if not texts:
        # No data — return market price with very low confidence
        return SignalResult(
            source="news",
            probability=market.yes_price,
            confidence=0.05,
            metadata={"article_count": 0, "sentiment": 0.0},
        )

    sentiments = [_sentiment_score(t) for t in texts]
    avg_sentiment = sum(sentiments) / len(sentiments)

    # Map sentiment [-1, +1] → probability shift ±15%
    shift = avg_sentiment * 0.15
    probability = max(0.02, min(0.98, market.yes_price + shift))

    # Confidence scales with article count and sentiment magnitude
    data_confidence = min(0.5, article_count / 20)
    signal_confidence = abs(avg_sentiment) * 0.4
    confidence = min(0.55, data_confidence + signal_confidence)

    return SignalResult(
        source="news",
        probability=probability,
        confidence=confidence,
        metadata={
            "article_count": article_count,
            "avg_sentiment": round(avg_sentiment, 3),
            "shift": round(shift, 3),
        },
    )
