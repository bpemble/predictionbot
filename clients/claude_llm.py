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

SYSTEM_PROMPT = """You are an expert forecaster with deep knowledge of prediction markets, statistics, and current events.

Your task is to estimate the probability that a prediction market resolves YES.

Guidelines:
- Be well-calibrated: a 70% prediction should be right ~70% of the time.
- Consider base rates, not just recent news.
- Anchor appropriately to the current market price — it reflects aggregate information.
- Distinguish between "this is unlikely" vs "I don't have enough information to know".
- When uncertain, your estimate should stay closer to the market price.
- Always call the submit_probability_estimate tool with your final answer."""


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
