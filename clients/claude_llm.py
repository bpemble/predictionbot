"""
Claude API wrapper for structured probability estimation.
Returns typed output via tool_use / structured output.
"""
from __future__ import annotations

import json
from typing import Optional

import anthropic

from config.settings import get_settings
from utils.logging import get_logger

log = get_logger(__name__)

PROBABILITY_TOOL = {
    "name": "submit_probability_estimate",
    "description": "Submit a calibrated probability estimate for a prediction market question.",
    "input_schema": {
        "type": "object",
        "properties": {
            "probability": {
                "type": "number",
                "description": "Probability (0.0–1.0) that the market resolves YES.",
            },
            "confidence": {
                "type": "number",
                "description": "Your confidence in this estimate (0.0–1.0). Use 0.3 for high uncertainty, 0.7+ for well-supported estimates.",
            },
            "reasoning": {
                "type": "string",
                "description": "Concise reasoning for the estimate (2–4 sentences).",
            },
            "key_factors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 5 key factors that drive the estimate.",
            },
        },
        "required": ["probability", "confidence", "reasoning"],
    },
}

SYSTEM_PROMPT = """You are a Superforecaster — a calibrated probabilistic reasoner trained in the methodology of Philip Tetlock's Good Judgment Project. Your estimates are used to trade prediction markets, so accuracy and calibration are paramount.

## Your Core Task
Estimate the probability a prediction market resolves YES. Your estimate will be compared to the market price; only deviate meaningfully when you have genuine informational edge.

## Methodology (apply in this order)

**Step 1 — Outside view / base rate**
Before reading any news, ask: what is the base rate for this CLASS of event?
- Sports game: home team wins ~55%, favorites beat the spread ~50–53%, heavy favorites win ~70–80%
- Incumbent leader re-election: ~65% globally, higher in stable democracies
- Fed rate change at any given meeting: ~35–40%; cuts require clear disinflation trend
- Ceasefire/peace agreement within 30 days: <10% unless negotiations are at advanced stage
- US legislation passing in divided Congress: <15% for major bills
- Company bankruptcy within 12 months: <5% for large-caps unless already distressed

**Step 2 — Resolution criteria analysis**
Read the resolution criteria carefully. Ask:
- Is the resolution event clearly defined, or is there ambiguity the crowd might be pricing wrong?
- Could a technicality cause a different outcome than the naive interpretation suggests?
- Is there a "50-50 on postponement/cancellation" clause that affects expected value?

**Step 3 — Inside view (news and evidence)**
Only shift from the base rate when you have concrete, specific evidence — not vibes:
- A poll showing 60% support for X is evidence; "widespread sentiment" is not
- An official announcement IS evidence; speculation about an announcement is not
- A definitive event having already occurred is near-certain; rumors are weak signal

**Step 4 — Calibrate your deviation**
The market price already reflects the crowd's best estimate. To deviate by more than 10pp, you need at least TWO of:
1. Base rate systematically different from implied probability
2. Resolution criteria the crowd is likely misreading
3. Concrete recent evidence not yet reflected in price
4. Cross-market logical inconsistency (sibling markets imply different probabilities)

## Calibration rules
- Never output round numbers (0.50, 0.60, 0.75) unless they are genuinely correct — they signal anchoring, not reasoning
- 0.50 should only appear when you genuinely have zero information beyond the market price
- Use the full probability scale: 0.03 for "nearly impossible", 0.15 for "unlikely", 0.35 for "possible but unlikely", 0.65 for "more likely than not", 0.85 for "probable", 0.95 for "nearly certain"
- Uncertainty ≠ 50%. "I don't know" means your estimate should equal the market price, not 50%

## Cognitive biases to actively correct
- **Availability bias:** Don't overweight vivid/dramatic recent events — they're already in the price
- **Narrative bias:** A compelling story doesn't increase probability; look for base rates
- **Recency bias:** Events from last week already moved the market; find what the market HASN'T priced
- **Conjunction fallacy:** "X wins AND Y happens" is always less likely than "X wins" alone
- **Anchoring:** Don't anchor on the market price when you have genuine contradicting evidence

## Confidence calibration
- confidence 0.3–0.4: You have weak signal, limited data, or high irreducible uncertainty
- confidence 0.5–0.6: You have moderate signal from 1–2 good sources
- confidence 0.7–0.8: You have strong signal from multiple independent sources
- confidence 0.8+: Reserve for cases where the outcome is nearly determined by known facts

Always call submit_probability_estimate with your final answer."""


class ClaudeLLMClient:
    def __init__(self) -> None:
        s = get_settings()
        self.client = anthropic.Anthropic(api_key=s.anthropic_api_key)
        self.fast_model = s.llm_model_fast
        self.deep_model = s.llm_model_deep

    def estimate_probability(
        self,
        question: str,
        description: str,
        resolution_date: Optional[str],
        market_price: float,
        context: str,
        deep: bool = False,
    ) -> dict:
        """
        Returns dict with keys: probability, confidence, reasoning, key_factors.
        Falls back to {"probability": market_price, "confidence": 0.1} on failure.
        """
        model = self.deep_model if deep else self.fast_model
        user_message = f"""**Prediction Market Question:**
{question}

**Resolution Criteria:**
{description or "Not specified."}

**Resolution Date:** {resolution_date or "Unknown"}

**Current Market Price (implied probability):** {market_price:.1%}

**Context (recent news, research, reference forecasts):**
{context or "No context available."}

---
Estimate the probability this resolves YES. Call submit_probability_estimate with your answer."""

        try:
            resp = self.client.messages.create(
                model=model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=[PROBABILITY_TOOL],
                tool_choice={"type": "any"},
                messages=[{"role": "user", "content": user_message}],
            )
            for block in resp.content:
                if block.type == "tool_use" and block.name == "submit_probability_estimate":
                    result = block.input
                    # Clamp probability to valid range
                    result["probability"] = max(0.01, min(0.99, float(result["probability"])))
                    result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.5))))
                    log.debug(
                        f"LLM estimate for '{question[:60]}': "
                        f"p={result['probability']:.2f} conf={result['confidence']:.2f}"
                    )
                    return result
        except Exception as exc:
            log.error(f"Claude API error: {exc}")

        # Fallback: return market price with low confidence
        return {
            "probability": market_price,
            "confidence": 0.1,
            "reasoning": "LLM unavailable — falling back to market price.",
            "key_factors": [],
        }
