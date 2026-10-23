"""Focused tests for OpenAILLM base_url, auth header, and response parsing.

Uses mock httpx transport — no network, no settings dependency.
"""

import json

import httpx
from jsonschema import validate

import pytest
from veryjudge.llm import OpenAILLM

SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string"}},
    "required": ["status"],
}


def _mock_llm(handler, api_key="sk-test", model="test-model", base_url=None):
    """Construct an OpenAILLM with a mock transport that calls handler(request)."""
    class MockTransport(httpx.BaseTransport):
        def handle_request(self, request):
            return handler(request)

    return OpenAILLM(
        api_key, model, base_url=base_url,
        client=httpx.Client(transport=MockTransport()),
    )


def test_default_endpoint_is_openai():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"output_text": '{"status":"ok"}'})

    llm = _mock_llm(handler)
    llm.complete("sys", "user", SCHEMA)
    assert captured["url"] == "https://api.openai.com/v1/responses"


@pytest.mark.parametrize("base_url,expected", [
    ("https://openrouter.ai/api", "https://openrouter.ai/api/v1/responses"),
    ("https://openrouter.ai/api/", "https://openrouter.ai/api/v1/responses"),
    ("https://example.com", "https://example.com/v1/responses"),
])
def test_custom_base_url(base_url, expected):
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"output_text": '{"status":"ok"}'})

    llm = _mock_llm(handler, base_url=base_url)
    llm.complete("s", "u", SCHEMA)
    assert captured["url"] == expected


def test_auth_header_format():
    captured = {}

    def handler(request):
        captured["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json={"output_text": '{"status":"ok"}'})

    llm = _mock_llm(handler, api_key="very-secret")
    llm.complete("s", "u", SCHEMA)
    assert captured["auth"] == "Bearer very-secret"


def test_payload_structure_unchanged():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.read().decode())
        return httpx.Response(200, json={"output_text": '{"status":"ok"}'})

    llm = _mock_llm(handler, model="veryjudge-test")
    llm.complete("sys prompt", '{"key":"val"}', SCHEMA)

    body = captured["body"]
    assert body["model"] == "veryjudge-test"
    assert body["instructions"] == "sys prompt"
    assert body["input"] == '{"key":"val"}'
    fmt = body["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["name"] == "veryjudge_result"
    assert fmt["strict"] is False
    assert fmt["schema"] == SCHEMA


def test_response_parsing_output_text():
    def handler(request):
        return httpx.Response(200, json={"output_text": '{"status":"ok"}'})

    llm = _mock_llm(handler)
    result = llm.complete("s", "u", SCHEMA)
    assert result == {"status": "ok"}


def test_response_parsing_output_array():
    def handler(request):
        return httpx.Response(200, json={
            "output": [{"content": [{"type": "output_text", "text": '{"status":"from_array"}'}]}],
        })

    llm = _mock_llm(handler)
    result = llm.complete("s", "u", SCHEMA)
    assert result == {"status": "from_array"}


def test_schema_validation_enforced():
    def handler(request):
        return httpx.Response(200, json={"output_text": '{"wrong_field": 1}'})

    llm = _mock_llm(handler)
    with pytest.raises(Exception):
        llm.complete("s", "u", SCHEMA)


def test_llm_base_url_from_settings(monkeypatch):
    """LLM_BASE_URL flows through load_settings into Settings."""
    from veryjudge.config import load_settings

    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api")
    monkeypatch.setenv("MODEL", "test-model")
    monkeypatch.setenv("OPERATOR_TOKEN", "op-token-12345")
    monkeypatch.setenv("VIEWER_TOKEN", "vw-token-67890")

    result = load_settings()
    assert result.llm_base_url == "https://openrouter.ai/api"


def test_llm_base_url_defaults_empty(monkeypatch):
    from veryjudge.config import load_settings

    # No LLM_BASE_URL set
    monkeypatch.setenv("MODEL", "test-model")
    monkeypatch.setenv("OPERATOR_TOKEN", "op-token-12345")
    monkeypatch.setenv("VIEWER_TOKEN", "vw-token-67890")

    result = load_settings()
    assert result.llm_base_url == ""
