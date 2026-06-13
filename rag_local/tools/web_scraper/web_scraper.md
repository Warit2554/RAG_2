# Web Scraper Tool Specification

The Web Scraper Tool fetches webpage content using `httpx` and parses the HTML layout to return clean, readable plaintext.

## Components
- **WebHTMLParser**: Custom parser subclassing Python's built-in `html.parser.HTMLParser` that strips script/style/nav/footer structures and formats headings and line breaks.
- **scrape_url**: Asynchronously downloads raw page HTML and processes it through `WebHTMLParser`.

## Methods
- `scrape_url(url, max_chars)`: Asynchronously scrapes URL content and limits output length to avoid LLM context overflow.
