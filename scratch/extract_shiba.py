#!/usr/bin/env python3
import sys
import json
import urllib.request
import urllib.parse
from pathlib import Path

def main():
    query = "shiba dog"
    print(f"Searching Wikimedia Commons for: {query}")
    
    # 1. Search Wikimedia Commons API
    api_url = (
        "https://commons.wikimedia.org/w/api.php?"
        "action=query&generator=search&gsrsearch=shiba+dog+photo"
        "&gsrnamespace=6&prop=imageinfo&iiprop=url&gsrlimit=10&format=json"
    )
    
    req = urllib.request.Request(
        api_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"Error fetching search results: {e}", file=sys.stderr)
        sys.exit(1)
        
    pages = data.get("query", {}).get("pages", {})
    image_urls = []
    for page in pages.values():
        info_list = page.get("imageinfo", [])
        for info in info_list:
            url = info.get("url")
            if url:
                image_urls.append(url)
                
    if not image_urls:
        print("No images found.", file=sys.stderr)
        sys.exit(1)
        
    print(f"Found {len(image_urls)} image URLs. Attempting to download the first one...")
    img_url = image_urls[0]
    print(f"Selected URL: {img_url}")
    
    # Get extension
    parsed_path = urllib.parse.urlparse(img_url).path
    ext = Path(parsed_path).suffix or ".jpg"
    
    output_dir = Path("/workspace")
    output_file = output_dir / f"shiba_dog{ext}"
    
    # 2. Download the image
    img_req = urllib.request.Request(
        img_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )
    
    try:
        with urllib.request.urlopen(img_req) as response:
            img_data = response.read()
            output_file.write_bytes(img_data)
        print(f"Successfully downloaded and saved to: {output_file}")
    except Exception as e:
        print(f"Error downloading image: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
