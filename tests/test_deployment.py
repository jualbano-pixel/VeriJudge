from fastapi.testclient import TestClient

from tests.helpers import TEXT, candidate
from tests.test_vj006 import claim_response, repository, settings
from tests.test_vj008_loop import runner
from veryjudge.app import create_app
from veryjudge.config import load_settings
from veryjudge.llm import StubLLM
from veryjudge.runner import StubEvidenceExtractor


def test_public_page_is_token_free_and_minimal(repository):
    with TestClient(create_app(settings(), database=repository)) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "Check claim" in response.text
    assert "Static token" not in response.text
    assert "Review panel" not in response.text


def test_public_check_runs_inline_and_returns_sources(repository):
    def factory(event):
        instance = runner(extractor=StubEvidenceExtractor({
            "https://example.test/1/0": [candidate().model_dump()]
        }))
        instance.event = event
        return instance

    llm = StubLLM({"default": claim_response(high_risk_category=None)})
    with TestClient(create_app(settings(), llm, repository, runner_factory=factory)) as client:
        response = client.post("/api/check", json={"text": TEXT})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["machine_status"] == "SUPPORTED"
    assert len(result["evidence"]) == 1
    assert result["documents"][0]["url"] == "https://example.test/1/0"
    assert "reviews" not in result and "trace" not in result and "rule_trace" not in result


def test_hosted_database_url_and_provider_secrets_from_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@db.example.test/app?sslmode=require")
    monkeypatch.setenv("MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "llm-secret")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "search-secret")
    monkeypatch.setenv("OPERATOR_TOKEN", "operator-secret")
    monkeypatch.setenv("VIEWER_TOKEN", "viewer-secret")
    result = load_settings()
    assert result.database_url.startswith("postgresql+psycopg://")
    assert result.model == "test-model"
    assert result.provider_keys == {"web": "search-secret", "llm": "llm-secret"}
