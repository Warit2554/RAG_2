from mcp.server.fastmcp import FastMCP
import asyncio

mcp = FastMCP("Search & Fetch")

@mcp.tool()
async def search(query: str, limit: int = 5) -> str:
    """Search the web using local search (SearXNG/DuckDuckGo) and return titles, URLs, and snippets."""
    from rag_local.tools.web_search.tool import local_web_search
    results = await local_web_search(query, limit)
    return "\n".join(f"- {r.title} ({r.url}): {r.content}" for r in results) or "No search results found."

@mcp.tool()
async def fetch(url: str, max_chars: int = 12000) -> str:
    """Fetch and extract clean text content from a given URL."""
    from rag_local.tools.web_scraper.tool import scrape_url
    return await scrape_url(url, max_chars)

if __name__ == "__main__":
    mcp.run()
