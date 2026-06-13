# Image Downloader Tool Specification

The Image Downloader Tool downloads images from specified URLs or extracts image links from containing web pages and saves them to a target directory.

## Components
- **download_image**: Asynchronously loads a URL, inspects content type, extracts image elements (e.g. OpenGraph tags, img tags) if the page is HTML, and writes the resulting binary payload to the local drive.
