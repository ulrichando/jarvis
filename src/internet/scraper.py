"""JARVIS Web Scraper — extract content from web pages."""

import requests
from bs4 import BeautifulSoup


def fetch_page(url: str, timeout: int = 10) -> str | None:
    """Fetch a web page and return its text content."""
    try:
        headers = {"User-Agent": "JARVIS/1.0"}
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove scripts, styles, nav, footer
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        # Collapse multiple newlines
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines[:200])  # Cap at 200 lines
    except Exception as e:
        return f"Failed to fetch: {e}"


def extract_links(url: str) -> list[dict]:
    """Extract all links from a page."""
    try:
        resp = requests.get(url, headers={"User-Agent": "JARVIS/1.0"}, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http"):
                links.append({"text": a.get_text(strip=True)[:100], "url": href})
        return links[:50]
    except Exception:
        return []
