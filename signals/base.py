from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SignalResult:
    source: str                     # 'llm' | 'news' | 'research' | 'metaculus' | 'gdelt'
    probability: float              # 0–1, YES probability
    confidence: float               # 0–1, self-reported confidence
    metadata: dict = field(default_factory=dict)  # source-specific extras

    def is_valid(self) -> bool:
        return 0.0 <= self.probability <= 1.0 and 0.0 <= self.confidence <= 1.0
