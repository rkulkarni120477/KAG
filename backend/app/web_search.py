"""Online fallback: Wikipedia (MediaWiki) search plus an optional self-hosted SearXNG instance.

Results are cached into the tenant's local curriculum store by retrieval.retrieve().
- Wikipedia: always used when web search is enabled. No API key.
- SearXNG: used when SEARXNG_URL is set (e.g. http://searxng:8080). It must have the
  "json" format enabled in its settings.yml.
Set WEB_SEARCH_ENABLED=false to disable the fallback. WIKIPEDIA_LANG selects the
language edition (default "en"). Each provider fails independently; a failure
returns no results from that provider so local answers keep working.
"""
import hashlib
import html
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "KAG-education-demo/1.0 (local curriculum assistant)"
_TAG = re.compile(r"<[^>]+>")


def enabled() -> bool:
    return os.getenv("WEB_SEARCH_ENABLED", "true").strip().lower() not in {"false", "0", "no", "off"}


def search(query: str, limit: int = 5) -> list[dict]:
    if not enabled() or not query.strip():
        return []
    now = datetime.now(timezone.utc).isoformat()
    sources = [_wikipedia(query, limit, now)]
    if os.getenv("SEARXNG_URL"):
        sources.append(_searxng(query, limit, now))
    # Interleave providers so one cannot fill the whole limit; de-duplicate by URL.
    rows, seen = [], set()
    for batch in zip(*[s + [None] * limit for s in sources]):
        for row in batch:
            if row and row["source_uri"] not in seen:
                seen.add(row["source_uri"])
                rows.append(row)
    return rows[:limit]


def _row(url: str, title: str, text: str, now: str) -> dict | None:
    title = html.unescape(_TAG.sub("", title or "")).strip()
    text = html.unescape(_TAG.sub("", text or "")).strip()
    if not url.startswith(("http://", "https://")) or not title or not text:
        return None
    return {
        "id": "web-" + hashlib.sha256(url.encode()).hexdigest()[:24],
        "title": title,
        "text": text,
        "source_uri": url,
        "retrieved_at": now,
    }


def _wikipedia(query: str, limit: int, now: str) -> list[dict]:
    lang = os.getenv("WIKIPEDIA_LANG", "en")
    try:
        response = httpx.get(
            f"https://{lang}.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": query, "srlimit": limit,
                    "srprop": "snippet", "format": "json", "formatversion": 2},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=5.0,
        )
        response.raise_for_status()
        items = response.json().get("query", {}).get("search", [])
    except Exception:
        logger.exception("Wikipedia search failed; continuing without it")
        return []
    items = items[:limit]
    intros = _wikipedia_intros([item.get("title", "") for item in items], lang)
    rows = []
    for item in items:
        title = item.get("title", "")
        url = f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}" if title else ""
        # Prefer the full plain-text article intro; fall back to the search snippet.
        row = _row(url, title, intros.get(title) or item.get("snippet", ""), now)
        if row:
            rows.append(row)
    return rows


def _wikipedia_intros(titles: list[str], lang: str) -> dict[str, str]:
    titles = [t for t in titles if t]
    if not titles:
        return {}
    try:
        response = httpx.get(
            f"https://{lang}.wikipedia.org/w/api.php",
            params={"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
                    "exchars": 1500, "titles": "|".join(titles), "format": "json", "formatversion": 2},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=5.0,
        )
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", [])
    except Exception:
        logger.exception("Wikipedia intro fetch failed; using search snippets")
        return {}
    return {p["title"]: p["extract"] for p in pages if p.get("extract")}


def _searxng(query: str, limit: int, now: str) -> list[dict]:
    base = os.environ["SEARXNG_URL"].rstrip("/")
    try:
        response = httpx.get(
            f"{base}/search",
            params={"q": query, "format": "json", "language": "en"},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=8.0,
        )
        response.raise_for_status()
        items = response.json().get("results", [])
    except Exception:
        logger.exception("SearXNG search failed; continuing without it")
        return []
    rows = []
    for item in items[:limit]:
        row = _row(item.get("url", ""), item.get("title", ""), item.get("content", ""), now)
        if row:
            rows.append(row)
    return rows
