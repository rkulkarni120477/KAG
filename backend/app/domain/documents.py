"""Document rules shared by the source service (identity, entities) and the worker (chunking).

Pure functions only: no I/O, so they are testable without any service running.
"""
import hashlib
import re
import unicodedata

TITLE_LIMITS = (3, 180)
TEXT_LIMITS = (30, 50000)
CHUNK_CHARS = 600
CHUNK_OVERLAP_SENTENCES = 1
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def validate(title: str, text: str) -> tuple[str, str]:
    title = _CONTROL.sub("", unicodedata.normalize("NFC", title or "")).strip()
    text = _CONTROL.sub("", unicodedata.normalize("NFC", text or ""))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not TITLE_LIMITS[0] <= len(title) <= TITLE_LIMITS[1]:
        raise ValueError("Title length is outside the allowed range")
    if not TEXT_LIMITS[0] <= len(text) <= TEXT_LIMITS[1]:
        raise ValueError("Text length is outside the allowed range")
    return title, text


def content_resource_id(tenant_id: str, title: str, text: str) -> str:
    # Tenant is part of the hash so identical content in two tenants never shares a row.
    digest = hashlib.sha256(f"{title.lower()}\n{text}".encode()).hexdigest()
    return "res-" + hashlib.sha256(f"{tenant_id}|{digest}".encode()).hexdigest()[:24]


def chunk_id(resource_id: str, ordinal: int) -> str:
    return f"{resource_id}#{ordinal}"


def extract_entities(text: str, skills: list[dict], skill_id: str | None) -> list[str]:
    """Rule-based entity linking: explicit skill_id plus skill names found in the text."""
    lowered = text.lower()
    found = {s["id"] for s in skills if s["name"].lower() in lowered}
    if skill_id:
        found.add(skill_id)
    return sorted(found)


def chunk_text(text: str, max_chars: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP_SENTENCES) -> list[str]:
    """Split on sentence boundaries into chunks of at most max_chars, repeating `overlap` sentences.

    A single sentence longer than max_chars is split on whitespace so no chunk is oversized.
    """
    sentences = [s for s in _SENTENCE.split(text.strip()) if s]
    pieces: list[str] = []
    for sentence in sentences:
        while len(sentence) > max_chars:
            cut = sentence.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            pieces.append(sentence)
    chunks: list[str] = []
    current: list[str] = []
    fresh = 0  # pieces in `current` that are not carried-over overlap
    for piece in pieces:
        if current and len(" ".join(current + [piece])) > max_chars:
            if fresh:
                chunks.append(" ".join(current))
                current = current[-overlap:] if overlap else []
                fresh = 0
            # Carried overlap must never push a chunk past the limit.
            if current and len(" ".join(current + [piece])) > max_chars:
                current = []
        current.append(piece)
        fresh += 1
    if fresh:
        chunks.append(" ".join(current))
    return chunks
