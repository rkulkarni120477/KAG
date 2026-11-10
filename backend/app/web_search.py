"""Online fallback using the Wikipedia (MediaWiki) search API. No API key is required.

Results are cached into the tenant's local curriculum store by retrieval.retrieve().
Set WEB_SEARCH_ENABLED=false to disable. WIKIPEDIA_LANG selects the language edition
(default "en"). Failures return no results so the local answer path keeps working.
"""
import hashlib
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
        results = response.json().get("query", {}).get("search", [])
    except Exception:
        logger.exception("Wikipedia search failed; continuing with local results only")
        return []
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for item in results[:limit]:
        title = item.get("title", "").strip()
        text = _TAG.sub("", item.get("snippet", "")).strip()
        if not title or not text:
            continue
        url = f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
        rows.append({
            "id": "web-" + hashlib.sha256(url.encode()).hexdigest()[:24],
            "title": title,
            "text": text,
            "source_uri": url,
            "retrieved_at": now,
        })
    return rows
