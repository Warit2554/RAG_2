from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import urllib.parse
import httpx

from rag_local.config import SETTINGS


@dataclass
class SearchResult:
    title: str
    url: str
    content: str


class DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[SearchResult] = []
        self.current_result: SearchResult | None = None
        self.in_title = False
        self.in_snippet = False
        self.title_text: list[str] = []
        self.snippet_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")
        if tag == "a" and "result__a" in cls:
            self.in_title = True
            self.title_text = []
            href = attrs_dict.get("href", "")
            parsed_url = urllib.parse.urlparse(href)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            uddg = query_params.get("uddg")
            real_url = uddg[0] if uddg else href
            if real_url.startswith("//"):
                real_url = "https:" + real_url
            self.current_result = SearchResult(url=real_url, title="", content="")
        elif tag == "a" and "result__snippet" in cls:
            self.in_snippet = True
            self.snippet_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.in_title:
            self.in_title = False
            if self.current_result:
                self.current_result.title = "".join(self.title_text).strip()
        elif tag == "a" and self.in_snippet:
            self.in_snippet = False
            if self.current_result:
                self.current_result.content = "".join(self.snippet_text).strip()
                self.results.append(self.current_result)
                self.current_result = None

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_text.append(data)
        elif self.in_snippet:
            self.snippet_text.append(data)


async def local_web_search(query: str, limit: int = 5) -> list[SearchResult]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    
    # Try local SearXNG service first
    try:
        params = {"q": query, "format": "json", "language": "en", "categories": "general"}
        async with httpx.AsyncClient(timeout=5.0) as client:
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
        if results:
            return results
    except Exception:
        # Fall back to DuckDuckGo search
        pass

    # DuckDuckGo fallback query
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers=headers,
                follow_redirects=True,
            )
            resp.raise_for_status()
            
            parser = DuckDuckGoParser()
            parser.feed(resp.text)
            return parser.results[:limit]
    except Exception as exc:
        raise RuntimeError(f"Web search failed (SearXNG down and DuckDuckGo fallback error): {exc}")
