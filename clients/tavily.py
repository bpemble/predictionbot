"""
Tavily search client — purpose-built for LLM context assembly.
Returns AI-synthesized summaries + source snippets per query.
Significantly better signal quality than keyword-match news APIs for LLM consumption.
"""
from __future__ import annotations

from config.settings import get_settings
from utils.logging import get_logger
from utils.retry import with_retry

log = get_logger(__name__)


class TavilyClient:
    def __init__(self) -> None:
        self.key = get_settings().tavily_api_key
        self._client = None

    def available(self) -> bool:
        return bool(self.key)

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from tavily import TavilyClient as _Tavily
            self._client = _Tavily(api_key=self.key)
            return self._client
        except ImportError:
            raise ImportError("tavily-python not installed. Run: pip install tavily-python")

    @with_retry(max_retries=1, backoff_max=5.0)
    def search(self, query: str, num_results: int = 5) -> list[dict]:
        """
        Returns list of {title, url, content, score} dicts.
        Prepends an AI-synthesized answer if Tavily provides one.
        """
        if not self.key:
            return []
        try:
            client = self._get_client()
            response = client.search(
                query=query,
                search_depth="advanced",
                max_results=num_results,
                include_answer=True,
            )
            results = []
            if response.get("answer"):
                results.append({
                    "title": "AI Summary",
                    "url": "",
                    "content": response["answer"],
                    "score": 1.0,
                })
            for r in response.get("results", []):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                    "score": r.get("score", 0.0),
                })
            return results
        except Exception as exc:
            log.warning(f"Tavily search failed: {exc}")
            return []

    def format_for_context(self, results: list[dict]) -> str:
        if not results:
            return ""
        lines = []
        for r in results:
            if r["title"] == "AI Summary":
                lines.append(f"Summary: {r['content'][:500]}")
            else:
                snippet = r["content"][:300].replace("\n", " ")
                lines.append(f"- [{r['title']}] {snippet}")
        return "\n".join(lines)
