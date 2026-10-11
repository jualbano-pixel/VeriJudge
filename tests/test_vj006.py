import hashlib
import os

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from veryjudge.app import create_app
from veryjudge.config import Settings
from veryjudge.db import Database
from veryjudge.llm import StubLLM
from tests.helpers import TEXT

AUTH = {"Authorization": "Bearer test-operator"}


@pytest.fixture
def repository():
    db = Database(os.environ["TEST_DATABASE_URL"])
    yield db
    db.engine.dispose()


def settings():
    return Settings(OPERATOR_TOKEN="test-operator", VIEWER_TOKEN="test-viewer", database_url=os.environ["TEST_DATABASE_URL"])


def claim_response(**changes):
    return {"propositions": [{**dict(text=TEXT, proposition_type="factual", type_reason="A dated census count",
        claim_type="statistic", time_anchor="2020", entities=["Philippine", "109,035,343"],
        high_risk_category="health", suggested_material=True), **changes}]}


def test_stub_extraction_persists_every_field(repository):
    raw = "  " + TEXT + "  "
    llm = StubLLM({"default": claim_response()})
    with TestClient(create_app(settings(), llm, repository)) as client:
        response = client.post("/submissions", json={"text": raw}, headers=AUTH)
    assert response.status_code == 200, response.text
    value = response.json()
    p = value["propositions"][0]
    assert p["text"] == TEXT and p["original_text"] == TEXT and p["text_normalized"] == TEXT.lower()
    assert p["proposition_type"] == "factual" and p["claim_type"] == "statistic" and p["time_anchor"] == "2020"
    assert p["type_reason"] == "A dated census count" and p["suggested_material"]
    assert p["entities"] == ["Philippine", "109,035,343"] and p["high_risk_category"] == "health" and p["high_risk"]
    assert not p["operator_edited"] and not p["selected"] and not p["extraction_flag"]
    with repository.transaction() as conn:
        submission = repository.get(conn, "submissions", value["submission_id"])
        assert submission["text"] == raw and submission["text_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
        assert submission["extraction_status"] == "ok" and submission["extraction_error"] is None
    assert "created_at" in llm.calls[0]["user"]


def test_extraction_failure_stored(repository):
    raw = "This input is intentionally given a malformed model reply."
    with TestClient(create_app(settings(), StubLLM({"default": {}}), repository)) as client:
        response = client.post("/submissions", json={"text": raw}, headers=AUTH)
    assert response.status_code == 502
    with repository.transaction() as conn:
        table = repository.tables["submissions"]
        row = conn.execute(table.select().where(table.c.text == raw)).mappings().first()
        assert row["extraction_status"] == "failed" and row["extraction_error"]


@pytest.mark.parametrize("raw", ["", "too short", "x"*1001])
def test_submission_validation(repository, raw):
    with TestClient(create_app(settings(), database=repository)) as client:
        assert client.post("/submissions", json={"text": raw}, headers=AUTH).status_code == 400


def test_invalid_anchor_stored_null(repository):
    with TestClient(create_app(settings(), StubLLM({"default": claim_response(time_anchor="next month")}), repository)) as client:
        response = client.post("/submissions", json={"text": TEXT}, headers=AUTH)
        assert response.json()["propositions"][0]["time_anchor"] is None


def test_url_only_accepted(repository):
    with TestClient(create_app(settings(), StubLLM({"default": claim_response(proposition_type="vague")}), repository)) as client:
        assert client.post("/submissions", json={"text": "https://example.test/claim"}, headers=AUTH).status_code == 200
