from .base import SignalResult
from .aggregator import aggregate, AggregationResult
from . import llm_signal, news_signal, research_signal, metaculus_signal, gdelt_signal

__all__ = [
    "SignalResult", "aggregate", "AggregationResult",
    "llm_signal", "news_signal", "research_signal",
    "metaculus_signal", "gdelt_signal",
]
