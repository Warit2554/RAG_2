from __future__ import annotations

import mimetypes
import re
from pathlib import Path
import urllib.parse
import httpx


# Wikimedia requires a descriptive User-Agent to avoid rate limits
_USER_AGENT = (
    "NexusRAG/1.0 (local AI assistant; image downloader) "
    "Mozilla/5.0 AppleWebKit/537.36 Chrome/120.0.0.0"
)
_WIKIMEDIA_AGENT = "NexusRAG/1.0 (local-ai-assistant; image-downloader) python-httpx"


async def download_image(url: str, save_dir: str) -> str:
    """Downloads an image from a URL, extracting direct links from webpage containers if needed.
    
    Returns the absolute path to the saved file.
    """
    save_path = Path(save_dir).expanduser().resolve()
    save_path.mkdir(parents=True, exist_ok=True)

    is_wikimedia = "wikimedia.org" in url or "wikipedia.org" in url
    headers = {
        "User-Agent": _WIKIMEDIA_AGENT if is_wikimedia else _USER_AGENT,
    }
    if is_wikimedia:
        headers["Api-User-Agent"] = _WIKIMEDIA_AGENT

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            
            content_type = resp.headers.get("Content-Type", "")
            
            # Case 1: The response is directly an image
            if "image/" in content_type:
                return _save_bytes(resp.content, content_type, url, save_path)
            
            # Case 2: The response is HTML, search for image URLs in metadata/tags
            html = resp.text
            
            # Check og:image first
            og_match = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
            if not og_match:
                og_match = re.search(r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:image["\']', html, re.IGNORECASE)
                
            img_url = og_match.group(1) if og_match else None
            
            # Check twitter:image if no og:image
            if not img_url:
                twitter_match = re.search(r'<meta\s+(?:name|property)=["\']twitter:image["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
                if twitter_match:
                    img_url = twitter_match.group(1)
            
            # Fall back to first large img tag src — skip logos/icons/banners
            if not img_url:
                src_matches = re.findall(r'<img\s+[^>]*src=["\']([^"\']+)["\']', html, re.IGNORECASE)
                for src in src_matches:
                    low = src.lower()
                    if not any(bad in low for bad in ["logo", "icon", "banner", "avatar", "sprite", "tracking", "pixel"]):
                        img_url = src
                        break
                if not img_url and src_matches:
                    img_url = src_matches[0]
            
            if not img_url:
                return "Error: Could not extract any image link from the webpage."

            # Resolve relative URLs
            img_url = urllib.parse.urljoin(url, img_url)
            
            # Fetch the actual image
            img_resp = await client.get(img_url, headers=headers)
            img_resp.raise_for_status()
            img_content_type = img_resp.headers.get("Content-Type", "")
            
            if "image/" not in img_content_type:
                return f"Error: Extracted URL '{img_url}' did not return an image content type (got '{img_content_type}')."
                
            return _save_bytes(img_resp.content, img_content_type, img_url, save_path)

    except Exception as exc:
        return f"Error downloading from '{url}': {exc}"


async def search_for_image_url(query: str) -> str | None:
    """Search for a direct image URL (.jpg/.png/.webp/.gif) matching the query.

    Strategies (in order):
    1. Wikimedia Commons search API — returns direct CDN image URLs, most reliable
    2. DuckDuckGo Images JSON via vqd token
    3. DuckDuckGo HTML results → scrape og:image from each page

    Returns the first usable direct image URL, or None.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    IMAGE_EXTS = re.compile(r'\.(jpg|jpeg|png|webp|gif|bmp)(\?|$)', re.IGNORECASE)
    BAD_KEYWORDS = re.compile(r'logo|icon|banner|avatar|sprite|tracking|pixel|1x1|placeholder', re.IGNORECASE)
    candidates: list[str] = []

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:

        # Strategy 1: Wikimedia Commons API — returns real CDN image URLs directly
        try:
            resp = await client.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": f"{query} photo",
                    "gsrnamespace": "6",
                    "prop": "imageinfo",
                    "iiprop": "url",
                    "gsrlimit": "10",
                    "format": "json",
                },
                headers={"User-Agent": _WIKIMEDIA_AGENT, "Api-User-Agent": _WIKIMEDIA_AGENT},
            )
            data = resp.json()
            pages = data.get("query", {}).get("pages", {})
            for page in list(pages.values()):
                info = page.get("imageinfo", [{}])[0]
                img_url = info.get("url", "")
                if img_url and IMAGE_EXTS.search(img_url) and not BAD_KEYWORDS.search(img_url):
                    candidates.append(img_url)
                    if len(candidates) >= 5:
                        break
        except Exception:
            pass

        if candidates:
            return candidates[0]

        # Strategy 2: DuckDuckGo Images JSON via vqd token
        try:
            resp = await client.get(
                "https://duckduckgo.com/",
                params={"q": query, "iax": "images", "ia": "images"},
                headers=headers,
            )
            vqd_match = re.search(r'vqd=(["\'])([^"\']+)\1', resp.text)
            if vqd_match:
                vqd = vqd_match.group(2)
                img_resp = await client.get(
                    "https://duckduckgo.com/i.js",
                    params={"q": query, "vqd": vqd, "o": "json", "l": "us-en", "s": "0", "f": ",,,,,"},
                    headers={**headers, "Referer": "https://duckduckgo.com/"},
                )
                data = img_resp.json()
                for result in data.get("results", []):
                    img_url = result.get("image") or result.get("thumbnail")
                    if img_url and IMAGE_EXTS.search(img_url) and not BAD_KEYWORDS.search(img_url):
                        return img_url
        except Exception:
            pass

        # Strategy 3: DuckDuckGo HTML results → scrape og:image from result pages
        try:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers=headers,
            )
            page_urls = re.findall(r'href=["\']((https?://)[^"\']+)["\']', resp.text)
            for (page_url, _) in page_urls[:8]:
                if any(x in page_url for x in ["duckduckgo", "javascript:", "mailto:"]):
                    continue
                try:
                    pr = await client.get(page_url, headers=headers, timeout=8.0)
                    pr.raise_for_status()
                    og = re.search(
                        r'<meta[^>]+property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']'
                        r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']',
                        pr.text, re.IGNORECASE
                    )
                    if og:
                        candidate = og.group(1) or og.group(2)
                        if candidate and IMAGE_EXTS.search(candidate) and not BAD_KEYWORDS.search(candidate):
                            return urllib.parse.urljoin(page_url, candidate)
                except Exception:
                    continue
        except Exception:
            pass

    return None


def _save_bytes(content: bytes, content_type: str, url: str, target_dir: Path) -> str:
    # Get extension based on Content-Type
    ext = mimetypes.guess_extension(content_type.split(";")[0])
    if not ext:
        # fallback to parsing extension from URL
        parsed_path = urllib.parse.urlparse(url).path
        ext = Path(parsed_path).suffix
        if not ext or len(ext) > 5:
            ext = ".jpg" # safe default
            
    # Clean filename
    base_name = "downloaded_image"
    
    # Avoid overwriting files
    dest = target_dir / f"{base_name}{ext}"
    counter = 1
    while dest.exists():
        dest = target_dir / f"{base_name}_{counter}{ext}"
        counter += 1
        
    dest.write_bytes(content)
    return f"Success! Saved image to {dest.resolve()}"
