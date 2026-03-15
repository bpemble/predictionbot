"""Exa.ai semantic search client."""
from __future__ import annotations

from config.settings import get_settings
from utils.logging import get_logger
from utils.retry import with_retry

log = get_logger(__name__)


class ExaClient:
    def __init__(self) -> None:
        self.key = get_settings().exa_api_key
        self._client = None

    def available(self) -> bool:
        return bool(self.key)

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from exa_py import Exa
            self._client = Exa(api_key=self.key)
            return self._client
        except ImportError:
            raise ImportError("exa-py not installed. Run: pip install exa-py")

    @with_retry()
    def search(self, query: str, num_results: int = 5, days_back: int = 7) -> list[dict]:
        """Returns list of {title, url, text, published_date} dicts."""
        if not self.key:
            return []
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            exa = self._get_client()
            results = exa.search_and_contents(
                query,
                num_results=num_results,
                start_published_date=since,
                text={"max_characters": 400},
            )
            return [
                {
                    "title": r.title or "",
                    "url": r.url or "",
                    "text": r.text or "",
                    "published_date": r.published_date or "",
                }
                for r in results.results
            ]
        except Exception as exc:
            log.warning(f"Exa search failed: {exc}")
            return []

    def format_for_context(self, results: list[dict]) -> str:
        if not results:
            return ""
        lines = []
        for r in results:
            snippet = r["text"][:200].replace("\n", " ")
            lines.append(f"- {r['title']}: {snippet}")
        return "\n".join(lines)
