"""Text normalization and the deterministic hashed embedding used by the local pilot.

Hash vectors are explicitly not a trained model. They keep retrieval reproducible and
offline; replace `embedding` with a governed embedding model before production.
"""
import hashlib
import math
import re

DIMENSION = 256


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def embedding(text: str) -> list[float]:
    result = [0.0] * DIMENSION
    for token in tokens(text):
        digest = hashlib.sha256(token.encode()).digest()
        result[int.from_bytes(digest[:2], "big") % DIMENSION] += 1 if digest[2] % 2 else -1
    norm = math.sqrt(sum(x * x for x in result)) or 1
    return [x / norm for x in result]
