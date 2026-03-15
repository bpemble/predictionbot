"""
Resolution Criteria Analyzer — the primary info-asymmetry signal.

The biggest structural edge in prediction markets is NOT speed.
It's reading the resolution criteria more carefully than the crowd.

Common alpha sources this signal finds:
  1. Ambiguous criteria — "will X happen?" where X has multiple interpretations
  2. Edge cases — unlikely scenarios the market hasn't priced
  3. Resolution source bias — markets that resolve on a specific source
     that may behave differently than the crowd expects
  4. Temporal quirks — "by end of Q2" vs "by June 30" discrepancies
  5. Definition disagreements — e.g. "recession" defined differently by
     various bodies; market may be pricing one while the resolution oracle
     uses another

Uses Claude Opus for deep reasoning (highest model, used selectively).
"""
from __future__ import annotations

import anthropic

from clients.polymarket import PolymarketClient
from config.settings import get_settings
from signals.base import SignalResult
from utils.logging import get_logger
from utils.normalizer import MarketSchema

log = get_logger(__name__)

_poly = PolymarketClient()

SYSTEM_PROMPT = """You are an expert in prediction market resolution and legal/definitional analysis.

Your job is to find INFORMATION ASYMMETRY — cases where careful reading of resolution criteria reveals that the market price is likely wrong because:
- The criteria are ambiguous and the crowd is assuming one interpretation
- There are edge cases or technicalities the market has overlooked
- The resolution source behaves differently than the crowd expects
- Historical base rates for this type of event differ from the implied probability

You are NOT trying to predict the news — you are trying to identify structural mispricings from the contract terms themselves.

Be highly specific. Vague reasoning ("this is uncertain") has no value. Identify the exact mechanism of mispricing if one exists."""

RESOLUTION_TOOL = {
    "name": "submit_resolution_analysis",
    "description": "Submit analysis of resolution criteria for a prediction market.",
    "input_schema": {
        "type": "object",
        "properties": {
            "probability": {
                "type": "number",
                "description": "Your probability estimate (0.0–1.0) that this resolves YES, accounting for resolution criteria nuances.",
            },
            "confidence": {
                "type": "number",
                "description": "Confidence in this estimate (0.0–1.0). Use 0.7+ only if you found a specific, concrete mispricing mechanism. Use 0.3 if you found no clear edge.",
            },
            "alpha_found": {
                "type": "boolean",
                "description": "True if you identified a concrete information-asymmetry opportunity. False if the market appears fairly priced from a resolution-criteria perspective.",
            },
            "alpha_mechanism": {
                "type": "string",
                "description": "If alpha_found=True: describe the exact mechanism (e.g. 'Resolution criteria requires unanimous vote but market is pricing simple majority'). If False: 'No resolution-criteria alpha identified.'",
            },
            "base_rate_note": {
                "type": "string",
                "description": "Historical base rate context for similar events, if relevant.",
            },
        },
        "required": ["probability", "confidence", "alpha_found", "alpha_mechanism"],
    },
}


def run(market: MarketSchema) -> SignalResult:
    """
    Deep analysis of resolution criteria for a single market.
    Only fires Claude Opus when resolution criteria are available.
    """
    # Fetch resolution criteria
    criteria = ""
    if market.platform == "polymarket" and market.id:
        try:
            criteria = _poly.get_resolution_criteria(market.id)
        except Exception as exc:
            log.debug(f"Could not fetch resolution criteria for {market.id}: {exc}")

    if not criteria:
        return SignalResult(
            source="resolution",
            probability=market.yes_price,
            confidence=0.05,
            metadata={"reason": "No resolution criteria available"},
        )

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    user_message = f"""Analyze this prediction market for information-asymmetry opportunities.

**Market Question:** {market.title}

**Resolution Criteria:**
{criteria}

**Current Market Price (implied YES probability):** {market.yes_price:.1%}
**Closes:** {market.resolution_date or 'Unknown'}
**Platform:** {market.platform}

Carefully read the resolution criteria. Is there anything about how this market will actually resolve that the crowd might be mispricing? Consider ambiguities, edge cases, base rates, and resolution source behavior.

Call submit_resolution_analysis with your findings."""

    try:
        resp = client.messages.create(
            model=settings.llm_model_deep,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=[RESOLUTION_TOOL],
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": user_message}],
        )
        for block in resp.content:
            if block.type == "tool_use" and block.name == "submit_resolution_analysis":
                result = block.input
                prob = max(0.01, min(0.99, float(result["probability"])))
                confidence = float(result.get("confidence", 0.3))
                alpha_found = bool(result.get("alpha_found", False))

                log.info(
                    f"Resolution analysis {market.title[:50]}: "
                    f"p={prob:.2f} conf={confidence:.2f} alpha={alpha_found} | "
                    f"{result.get('alpha_mechanism', '')[:80]}"
                )

                return SignalResult(
                    source="resolution",
                    probability=prob,
                    confidence=confidence,
                    metadata={
                        "alpha_found": alpha_found,
                        "alpha_mechanism": result.get("alpha_mechanism", ""),
                        "base_rate_note": result.get("base_rate_note", ""),
                        "criteria_length": len(criteria),
                    },
                )
    except Exception as exc:
        log.error(f"Resolution analyzer failed for {market.id}: {exc}")

    return SignalResult(
        source="resolution",
        probability=market.yes_price,
        confidence=0.05,
        metadata={"reason": "Analysis failed"},
    )
