# Web Search Tool Specification

The Web Search Tool provides search functionality across the web using a local SearXNG instance.

## Components
- **SearchResult**: A dataclass containing the page title, URL, and snippet content of each result.
- **local_web_search**: Asynchronously calls the configured SearXNG search endpoint to query indices and format the returned list.
