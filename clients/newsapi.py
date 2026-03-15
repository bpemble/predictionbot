"""NewsAPI client — fetches recent headlines relevant to a market question."""
from __future__ import annotations

from typing import Optional

import requests

from config.settings import get_settings
from utils.logging import get_logger
from utils.retry import with_retry

log = get_logger(__name__)
BASE = "https://newsapi.org/v2"


class NewsAPIClient:
    def __init__(self) -> None:
        self.key = get_settings().newsapi_key

    def available(self) -> bool:
        return bool(self.key)

    @with_retry()
    def search(self, query: str, days_back: int = 3, max_articles: int = 8) -> list[dict]:
        """Returns list of {title, description, url, publishedAt} dicts."""
        if not self.key:
            return []
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
        resp = requests.get(
            f"{BASE}/everything",
            params={
                "q": query,
                "from": since,
                "sortBy": "relevancy",
                "pageSize": max_articles,
                "language": "en",
                "apiKey": self.key,
            },
            timeout=20,
        )
        resp.raise_for_status()
        articles = resp.json().get("articles", [])
        return [
            {
                "title": a.get("title", ""),
                "description": a.get("description", ""),
                "url": a.get("url", ""),
                "published_at": a.get("publishedAt", ""),
                "source": a.get("source", {}).get("name", ""),
            }
            for a in articles
        ]

    def format_for_context(self, articles: list[dict]) -> str:
        if not articles:
            return "No recent news found."
        lines = []
        for a in articles:
            lines.append(f"- [{a['source']}] {a['title']}: {a['description']}")
        return "\n".join(lines)
