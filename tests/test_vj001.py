import os
import re
import subprocess
import sys

from fastapi.testclient import TestClient
from jsonschema import ValidationError
import pytest
from sqlalchemy import create_engine, inspect, text

from veryjudge.app import create_app
from veryjudge.config import Settings
from veryjudge.ids import new_id, PREFIXES
from veryjudge.llm import StubLLM


def test_health():
    settings = Settings(OPERATOR_TOKEN="test-operator", VIEWER_TOKEN="test-viewer",
                        database_url=os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://veryjudge:veryjudge@127.0.0.1:5432/veryjudge"))
    with TestClient(create_app(settings)) as client:
        assert client.get("/health", headers={"Authorization": "Bearer test-operator"}).json() == {"status": "ok"}
        assert client.get("/health").status_code == 404
        assert client.get("/health", headers={"Authorization": "Bearer test-viewer"}).status_code == 404


def test_stub_llm_returns_canned_json():
    llm = StubLLM({"test": {"value": 42}}, scenario="test")
    schema = {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}
    result = llm.complete("system", "user", schema)
    assert result == {"value": 42}
    result["value"] = 0
    assert llm.complete("system", "user", schema) == {"value": 42}
    with pytest.raises(ValidationError):
        llm.complete("", "", {"type": "array"})


def test_prefixed_ulids():
    for prefix in PREFIXES:
        values = {new_id(prefix) for _ in range(100)}
        assert len(values) == 100
        assert all(re.fullmatch(prefix + r"_[0-7][0-9A-HJKMNP-TV-Z]{25}", v) for v in values)


def test_frozen_budgets_and_tokens():
    base = dict(OPERATOR_TOKEN="a", VIEWER_TOKEN="b", database_url="unused")
    for key, value in [("max_searches", 9), ("max_captures", 11), ("max_fetch_attempts", 16), ("wall_clock_s", 181)]:
        with pytest.raises(ValueError):
            Settings(**base, **{key: value})
    with pytest.raises(ValueError):
        Settings(**{**base, "VIEWER_TOKEN": "a"})


@pytest.mark.postgres
def test_migration_and_constraints():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("VJ-001 gate requires TEST_DATABASE_URL pointing to disposable Postgres 15")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True,
                   env={**os.environ, "DATABASE_URL": url})
    engine = create_engine(url)
    with engine.connect() as connection:
        assert connection.scalar(text("SHOW server_version_num")).startswith("15")
        inspector = inspect(connection)
        assert set(inspector.get_table_names()) == {
            "submissions", "propositions", "research_runs", "retrieval_steps",
            "documents", "evidence", "judgments", "reviews", "alembic_version"}
        assert any(c["column_names"] == ["run_id"] for c in inspector.get_unique_constraints("judgments"))
        assert any(c["column_names"] == ["submission_id", "ordinal"] for c in inspector.get_unique_constraints("propositions"))
        assert any(c["column_names"] == ["run_id", "step_number"] for c in inspector.get_unique_constraints("retrieval_steps"))
        generated = next(c for c in inspector.get_columns("propositions") if c["name"] == "high_risk")
        assert generated["computed"]["persisted"]
        for table in ("propositions", "judgments"):
            assert any(i["unique"] and "postgresql_where" in i["dialect_options"] for i in inspector.get_indexes(table))
        assert len(inspector.get_check_constraints("evidence")) >= 12
        assert len(inspector.get_check_constraints("documents")) >= 5
        for table in ("submissions", "propositions", "retrieval_steps", "evidence", "judgments", "reviews"):
            created = next(c for c in inspector.get_columns(table) if c["name"] == "created_at")
            assert not created["nullable"] and "now()" in created["default"]
    engine.dispose()
