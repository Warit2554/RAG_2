from __future__ import annotations

from html.parser import HTMLParser
import httpx


class WebHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text_parts: list[str] = []
        self.ignore_tags = {
            "script", "style", "head", "nav", "footer", "header", 
            "aside", "iframe", "form", "button", "select", "option"
        }
        self.current_tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.current_tag_stack.append(tag.lower())
        # Add formatting markers for common layout tags
        t = tag.lower()
        if t in {"p", "div", "br", "li", "tr"}:
            self.text_parts.append("\n")
        elif t in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.text_parts.append(f"\n\n{'#' * int(t[1])} ")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if self.current_tag_stack:
            self.current_tag_stack.pop()
        if t in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        # If inside any ignored tag, skip the text content
        if any(ignored in self.current_tag_stack for ignored in self.ignore_tags):
            return
        cleaned = data.strip()
        if cleaned:
            self.text_parts.append(cleaned + " ")

    def get_text(self) -> str:
        raw_text = "".join(self.text_parts)
        # Collapse multiple blank lines
        lines = []
        for line in raw_text.splitlines():
            line_str = line.strip()
            if line_str:
                lines.append(line_str)
            elif lines and lines[-1] != "":
                lines.append("")
        return "\n".join(lines).strip()


async def scrape_url(url: str, max_chars: int = 12000) -> str:
    """Fetches a URL asynchronously and returns its main text content parsed as markdown-like text."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            html = resp.text
            
            parser = WebHTMLParser()
            parser.feed(html)
            text = parser.get_text()
            
            if len(text) > max_chars:
                return text[:max_chars] + f"\n\n...[Content truncated to {max_chars} characters]..."
            return text if text else "No readable text content extracted."
    except Exception as exc:
        return f"Error scraping URL '{url}': {exc}"
