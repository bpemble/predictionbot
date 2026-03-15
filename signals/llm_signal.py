"""
LLM signal: assembles context from news + Exa, then asks Claude to estimate probability.
This is the highest-weight signal in the aggregator.
"""
from __future__ import annotations

from clients.claude_llm import ClaudeLLMClient
from clients.exa import ExaClient
from clients.tavily import TavilyClient
from clients.polymarket import PolymarketClient
from signals.base import SignalResult
from utils.logging import get_logger
from utils.normalizer import MarketSchema

log = get_logger(__name__)

_llm = ClaudeLLMClient()
_tavily = TavilyClient()
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

    # 1b. Tavily: AI-synthesized search with source snippets (primary news source)
    if _tavily.available():
        tavily_results = _tavily.search(market.title, num_results=5)
        if tavily_results:
            context_parts.append("**Web Search & News:**\n" + _tavily.format_for_context(tavily_results))

    # 1c. Exa: semantic search as supplementary source
    if _exa.available():
        exa_results = _exa.search(market.title, num_results=3, days_back=7)
        if exa_results:
            context_parts.append("**Additional Sources:**\n" + _exa.format_for_context(exa_results))

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
