from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Mode ──────────────────────────────────────────────────────────────────
    paper_trade: bool = True

    # ── Polymarket ────────────────────────────────────────────────────────────
    poly_private_key: Optional[str] = None
    poly_api_key: Optional[str] = None
    poly_api_secret: Optional[str] = None
    poly_api_passphrase: Optional[str] = None
    poly_chain_id: int = 137

    @property
    def poly_host(self) -> str:
        return "https://clob.polymarket.com"

    @property
    def poly_gamma_host(self) -> str:
        return "https://gamma-api.polymarket.com"

    # ── Kalshi ────────────────────────────────────────────────────────────────
    kalshi_api_key_id: Optional[str] = None
    kalshi_private_key_path: str = "./secrets/kalshi_private.pem"

    @property
    def kalshi_base_url(self) -> str:
        if self.paper_trade:
            return "https://demo-api.kalshi.co"
        return "https://trading-api.kalshi.com"

    # ── LLM ──────────────────────────────────────────────────────────────────
    anthropic_api_key: Optional[str] = None
    llm_model_fast: str = "claude-sonnet-4-6"    # used for signal scoring
    llm_model_deep: str = "claude-opus-4-6"      # used for deep research on high-value trades

    # ── News & Research ───────────────────────────────────────────────────────
    tavily_api_key: Optional[str] = None
    exa_api_key: Optional[str] = None
    perplexity_api_key: Optional[str] = None

    # ── Capital ───────────────────────────────────────────────────────────────
    bankroll_poly_usd: float = 5000.0
    bankroll_kalshi_usd: float = 5000.0

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = "INFO"

    # ── DB ────────────────────────────────────────────────────────────────────
    @property
    def db_path(self) -> str:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "data", "bot.db")

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    def bankroll(self, platform: str) -> float:
        return self.bankroll_poly_usd if platform == "polymarket" else self.bankroll_kalshi_usd

    def polymarket_enabled(self) -> bool:
        return bool(self.poly_private_key)

    def kalshi_enabled(self) -> bool:
        return bool(self.kalshi_api_key_id) and os.path.exists(self.kalshi_private_key_path)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
