"""Perplexity sonar-pro client for deep research queries."""
from __future__ import annotations

import requests

from config.settings import get_settings
from utils.logging import get_logger
from utils.retry import with_retry

log = get_logger(__name__)
BASE = "https://api.perplexity.ai"


class PerplexityClient:
    def __init__(self) -> None:
        self.key = get_settings().perplexity_api_key

    def available(self) -> bool:
        return bool(self.key)

    @with_retry()
    def research(self, question: str, market_price: float) -> dict:
        """
        Returns dict with keys: summary (str), probability_hint (float|None), citations (list[str]).
        """
        if not self.key:
            return {"summary": "", "probability_hint": None, "citations": []}

        prompt = (
            f"You are a forecasting analyst. Research this prediction market question and "
            f"estimate the probability it resolves YES.\n\n"
            f"Question: {question}\n"
            f"Current market implied probability: {market_price:.1%}\n\n"
            f"Provide: (1) a brief factual summary of relevant evidence, "
            f"(2) your probability estimate as a decimal like 0.65, "
            f"(3) key sources. Be concise."
        )

        try:
            resp = requests.post(
                f"{BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "sonar-pro",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 600,
                    "return_citations": True,
                },
                timeout=45,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            citations = data.get("citations", [])

            # Parse a probability hint from the response text
            probability_hint = _extract_probability(content, market_price)

            return {
                "summary": content,
                "probability_hint": probability_hint,
                "citations": citations[:5],
            }
        except Exception as exc:
            log.warning(f"Perplexity research failed: {exc}")
            return {"summary": "", "probability_hint": None, "citations": []}


def _extract_probability(text: str, fallback: float) -> float:
    """Attempt to extract a probability decimal from Perplexity's text response."""
    import re
    # Look for patterns like "0.65", "65%", "probability of 0.7"
    patterns = [
        r"probability(?:\s+(?:of|estimate|is|:))?\s*(0\.\d+)",
        r"(0\.\d{1,2})\s*(?:probability|chance|likelihood)",
        r"(\d{1,2})%",
        r"\b(0\.[1-9]\d*)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            if val > 1:
                val /= 100
            if 0.02 <= val <= 0.98:
                return val
    return fallback
