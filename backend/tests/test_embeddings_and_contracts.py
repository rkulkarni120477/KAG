"""Embedding providers and typed contracts."""
import json
from unittest.mock import MagicMock, patch

import pydantic
import pytest

from app import embeddings
from app.contracts import AIDraft, QueryRequest


def test_hashed_embedding_is_deterministic_and_normalized():
    e = embeddings.HashedEmbedder()
    first = e.embed_query("Ratios compare two quantities")
    assert first == e.embed_query("Ratios compare two quantities")
    assert len(first) == embeddings.HASHED_DIMENSION
    assert abs(sum(x * x for x in first) - 1.0) < 1e-9
    assert e.is_trained_model is False


def test_bedrock_embedder_uses_titan_v2_request_shape_and_checks_dimensions():
    body = MagicMock()
    body.read.return_value = json.dumps({"embedding": [0.1] * 8}).encode()
    client = MagicMock()
    client.invoke_model.return_value = {"body": body}
    with patch("boto3.client", return_value=client):
        bedrock = embeddings.BedrockEmbedder("amazon.titan-embed-text-v2:0", "us-east-1", dimension=8)
        vector = bedrock.embed_query("tide pools")
    sent = json.loads(client.invoke_model.call_args.kwargs["body"])
    assert sent == {"inputText": "tide pools", "dimensions": 8, "normalize": True}
    assert len(vector) == 8
    assert bedrock.is_trained_model is True
    assert bedrock.name.startswith("bedrock-amazon-titan-embed-text-v2")


def test_bedrock_dimension_mismatch_is_an_error():
    body = MagicMock()
    body.read.return_value = json.dumps({"embedding": [0.1] * 5}).encode()
    client = MagicMock()
    client.invoke_model.return_value = {"body": body}
    with patch("boto3.client", return_value=client):
        bedrock = embeddings.BedrockEmbedder("amazon.titan-embed-text-v2:0", "us-east-1", dimension=8)
        with pytest.raises(RuntimeError, match="dimensions"):
            bedrock.embed_query("x")


def test_collection_name_depends_on_embedder_so_models_never_mix():
    a = embeddings.BedrockEmbedder("amazon.titan-embed-text-v2:0", "us-east-1", dimension=8)
    b = embeddings.BedrockEmbedder("amazon.titan-embed-text-v2:0", "us-east-1", dimension=16)
    assert a.name == b.name and a.dimension != b.dimension  # collection name includes the dimension
    assert embeddings.HashedEmbedder().name != a.name


def test_request_contract_rejects_unknown_fields():
    with pytest.raises(pydantic.ValidationError):
        QueryRequest(question="How do I prepare?", tenant_id="other")


def test_ai_draft_rejects_extra_fields_and_empty_citations():
    assert AIDraft(explanation="Supported.", evidence_ids=["a"]).evidence_ids == ["a"]
    with pytest.raises(pydantic.ValidationError):
        AIDraft(explanation="Supported.", evidence_ids=["a"], official_requirement="yes")
    with pytest.raises(pydantic.ValidationError):
        AIDraft(explanation="Supported.", evidence_ids=[])
