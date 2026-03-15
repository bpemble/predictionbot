"""
Maps heterogeneous platform market schemas to the internal MarketSchema.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class MarketSchema:
    id: str
    platform: str
    title: str
    yes_price: float
    no_price: float
    resolution_date: Optional[str] = None
    category: Optional[str] = None
    liquidity_usd: float = 0.0
    volume_usd: float = 0.0
    status: str = "open"
    outcome: Optional[str] = None
    # Platform-specific extras (not persisted to DB)
    yes_token_id: Optional[str] = None   # Polymarket only
    no_token_id: Optional[str] = None    # Polymarket only
    ticker: Optional[str] = None         # Kalshi only

    def to_db_dict(self) -> dict:
        return {
            "id": self.id,
            "platform": self.platform,
            "title": self.title,
            "category": self.category,
            "resolution_date": self.resolution_date,
            "yes_price": self.yes_price,
            "no_price": self.no_price,
            "liquidity_usd": self.liquidity_usd,
            "volume_usd": self.volume_usd,
            "status": self.status,
            "outcome": self.outcome,
        }

    def hours_to_close(self) -> Optional[float]:
        if not self.resolution_date:
            return None
        try:
            dt = datetime.fromisoformat(self.resolution_date.replace("Z", "+00:00"))
            delta = dt - datetime.now(timezone.utc)
            return delta.total_seconds() / 3600
        except Exception:
            return None


def normalize_polymarket(raw: dict) -> Optional[MarketSchema]:
    """Convert a Gamma API market dict to MarketSchema."""
    try:
        tokens = raw.get("tokens") or []
        yes_token = next((t for t in tokens if t.get("outcome", "").upper() == "YES"), None)
        no_token = next((t for t in tokens if t.get("outcome", "").upper() == "NO"), None)
        yes_price = float(yes_token.get("price", 0.5)) if yes_token else 0.5
        no_price = float(no_token.get("price", 0.5)) if no_token else 1.0 - yes_price

        return MarketSchema(
            id=raw["conditionId"],
            platform="polymarket",
            title=raw.get("question", ""),
            yes_price=yes_price,
            no_price=no_price,
            resolution_date=raw.get("endDateIso") or raw.get("end_date_iso"),
            category=raw.get("category"),
            liquidity_usd=float(raw.get("liquidity", 0) or 0),
            volume_usd=float(raw.get("volume", 0) or 0),
            status="open" if raw.get("active") and not raw.get("closed") else "closed",
            yes_token_id=yes_token.get("token_id") if yes_token else None,
            no_token_id=no_token.get("token_id") if no_token else None,
        )
    except Exception:
        return None


def normalize_kalshi(raw: dict) -> Optional[MarketSchema]:
    """Convert a Kalshi v2 market dict to MarketSchema."""
    try:
        # Kalshi prices are in cents (0-99); normalise to 0-1
        yes_price = float(raw.get("yes_bid", raw.get("last_price", 50))) / 100
        no_price = 1.0 - yes_price

        status_map = {"open": "open", "closed": "closed", "settled": "resolved"}
        status = status_map.get(raw.get("status", "open"), "open")

        outcome = None
        if status == "resolved":
            result = raw.get("result", "")
            outcome = "yes" if result.upper() == "YES" else "no" if result.upper() == "NO" else None

        return MarketSchema(
            id=raw["ticker"],
            platform="kalshi",
            title=raw.get("title", ""),
            yes_price=yes_price,
            no_price=no_price,
            resolution_date=raw.get("close_time"),
            category=raw.get("category"),
            liquidity_usd=float(raw.get("liquidity", 0) or 0),
            volume_usd=float(raw.get("volume", 0) or 0),
            status=status,
            outcome=outcome,
            ticker=raw["ticker"],
        )
    except Exception:
        return None
