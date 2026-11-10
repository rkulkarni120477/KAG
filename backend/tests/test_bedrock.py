"""Validate the generation branch without sending any network requests."""
import json
import os
from unittest.mock import Mock, patch

from app.kag import generate


def test_bedrock_keeps_ai_separate_and_rejects_unknown_citations():
    state = {
        "user":{"tenant_id":"test"},"question":"Explain ratios",
        "evidence":[{"id":"source-1","title":"Ratios","source_version":"demo-v1","text":"Ratios compare quantities.","source_uri":"demo://ratios","skill_id":"ratios"}],
        "path":{"steps":[],"student":None},
    }
    client = Mock()
    client.converse.return_value = {"stopReason":"end_turn","output":{"message":{"content":[{"text":json.dumps({"explanation":"Illustrative: ratios compare quantities.","evidence_ids":["source-1"]})}]}}}
    with patch.dict(os.environ,{"BEDROCK_MODEL_ID":"us.anthropic.claude-fable-5-2","AWS_REGION":"us-east-1"}), patch("app.kag.boto3.client",return_value=client):
        result = generate(state)["result"]
        assert result["generator"] == "Amazon Bedrock"
        assert result["ai_explanation"].startswith("Illustrative:")
        assert result["answer"] != result["ai_explanation"]
        assert client.converse.call_args.kwargs["additionalModelRequestFields"] == {"output_config":{"effort":"low"}}
        assert client.converse.call_args.kwargs["inferenceConfig"]["maxTokens"] == 2048
        client.converse.return_value["output"]["message"]["content"] = [{"text":json.dumps({"explanation":"Unsupported explanation","evidence_ids":["unknown-source"]})}]
        rejected = generate(state)["result"]
        assert rejected["generator"] == "Evidence-only local mode"
        assert rejected["ai_explanation"] is None
        assert rejected["warning"]
        client.converse.return_value["stopReason"] = "max_tokens"
        truncated = generate(state)["result"]
        assert truncated["generator"] == "Evidence-only local mode"
        assert truncated["ai_explanation"] is None
