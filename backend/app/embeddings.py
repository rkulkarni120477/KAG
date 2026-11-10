"""Embedding providers.

- Bedrock (default when BEDROCK_EMBED_MODEL_ID is set): Amazon Titan Text Embeddings V2, normalized
  1024-dimensional vectors. This is the production choice.
- Hashed (offline fallback): deterministic 256-dimensional hashed token vectors. Not a trained model;
  it keeps the pilot runnable without AWS and is labeled as such in the system status.

The provider name and dimension are part of the vector collection name, so vectors from different
models are never compared with each other.
"""
import hashlib
import json
import math
import os
import re
import threading

HASHED_DIMENSION = 256
TITAN_DIMENSION = int(os.getenv("BEDROCK_EMBED_DIMENSIONS", "1024"))


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class HashedEmbedder:
    name = "hashed-256"
    dimension = HASHED_DIMENSION
    is_trained_model = False

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        result = [0.0] * HASHED_DIMENSION
        for token in tokens(text):
            digest = hashlib.sha256(token.encode()).digest()
            result[int.from_bytes(digest[:2], "big") % HASHED_DIMENSION] += 1 if digest[2] % 2 else -1
        norm = math.sqrt(sum(x * x for x in result)) or 1
        return [x / norm for x in result]


class BedrockEmbedder:
    is_trained_model = True

    def __init__(self, model_id: str, region: str, dimension: int = TITAN_DIMENSION):
        import boto3
        from botocore.config import Config

        self.model_id = model_id
        self.dimension = dimension
        self.name = "bedrock-" + re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")[:40]
        self.client = boto3.client("bedrock-runtime", region_name=region,
                                   config=Config(connect_timeout=3, read_timeout=20, retries={"total_max_attempts": 3, "mode": "standard"}))

    def _invoke(self, text: str) -> list[float]:
        response = self.client.invoke_model(
            modelId=self.model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({"inputText": text, "dimensions": self.dimension, "normalize": True}),
        )
        vector = json.loads(response["body"].read())["embedding"]
        if len(vector) != self.dimension:
            raise RuntimeError(f"Embedding model returned {len(vector)} dimensions; expected {self.dimension}")
        return vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._invoke(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._invoke(text)


_lock = threading.Lock()
_embedder = None


def embedder():
    global _embedder
    with _lock:
        if _embedder is None:
            model = os.getenv("BEDROCK_EMBED_MODEL_ID", "").strip()
            if model:
                _embedder = BedrockEmbedder(model, os.getenv("AWS_REGION", "us-east-1"))
            else:
                _embedder = HashedEmbedder()
        return _embedder


def set_embedder(instance) -> None:
    """Tests inject a deterministic or mocked embedder."""
    global _embedder
    with _lock:
        _embedder = instance
