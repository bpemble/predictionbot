"""
LLM signal: assembles context from news + Exa, then asks Claude to estimate probability.
This is the highest-weight signal in the aggregator.
"""
from __future__ import annotations

from clients.claude_llm import ClaudeLLMClient
from clients.exa import ExaClient
from clients.newsapi import NewsAPIClient
from clients.polymarket import PolymarketClient
from signals.base import SignalResult
from utils.logging import get_logger
from utils.normalizer import MarketSchema

log = get_logger(__name__)

_llm = ClaudeLLMClient()
_news = NewsAPIClient()
_exa = ExaClient()
_poly = PolymarketClient()


def run(market: MarketSchema, deep: bool = False) -> SignalResult:
    """Generate an LLM-based probability estimate for the market."""
    context_parts = []

    # 1a. Fetch resolution criteria — critical for info-asymmetry edge
    resolution_criteria = ""
    if market.platform == "polymarket" and market.id:
        try:
            resolution_criteria = _poly.get_resolution_criteria(market.id)
        except Exception:
            pass

    if _news.available():
        articles = _news.search(market.title, days_back=5, max_articles=4)
        if articles:
            context_parts.append("**Recent News:**\n" + _news.format_for_context(articles))

    if _exa.available():
        exa_results = _exa.search(market.title, num_results=4, days_back=7)
        if exa_results:
            context_parts.append("**Web Search:**\n" + _exa.format_for_context(exa_results))

    context = "\n\n".join(context_parts) if context_parts else ""

    # 2. Ask Claude — now with full resolution criteria
    result = _llm.estimate_probability(
        question=market.title,
        description=resolution_criteria,
        resolution_date=market.resolution_date,
        market_price=market.yes_price,
        context=context,
        deep=deep,
    )

    return SignalResult(
        source="llm",
        probability=result["probability"],
        confidence=result.get("confidence", 0.5),
        metadata={
            "reasoning": result.get("reasoning", ""),
            "key_factors": result.get("key_factors", []),
            "model": _llm.deep_model if deep else _llm.fast_model,
            "context_chars": len(context),
        },
    )
