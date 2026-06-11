from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import SETTINGS


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    content: str


async def local_web_search(query: str, limit: int = 5) -> list[SearchResult]:
    params = {"q": query, "format": "json", "language": "en", "categories": "general"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(f"{SETTINGS.searxng_url.rstrip('/')}/search", params=params)
        response.raise_for_status()
        data = response.json()
    results = []
    for item in data.get("results", [])[:limit]:
        results.append(
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                content=item.get("content", ""),
            )
        )
    return results

