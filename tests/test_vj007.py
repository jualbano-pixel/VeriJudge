import threading

from fastapi.testclient import TestClient
import pytest

from veryjudge.app import create_app
from veryjudge.llm import StubLLM
from tests.test_vj006 import AUTH, claim_response, repository, settings
from tests.helpers import TEXT


@pytest.mark.parametrize("proposition_type", ["opinion", "prediction", "satire", "vague", "mixed"])
def test_extract_opinion_G10_shortcut(repository, proposition_type):
    with TestClient(create_app(settings(), StubLLM({"default": claim_response(proposition_type=proposition_type)}), repository)) as client:
        submission = client.post("/submissions", json={"text": TEXT}, headers=AUTH).json()
        pid = submission["propositions"][0]["id"]
        url = f"/submissions/{submission['submission_id']}/propositions/{pid}/select"
        response = client.post(url, json={"single_predicate_confirmed": True}, headers=AUTH)
        assert response.status_code == 200 and "run_id" not in response.json()
        with repository.transaction() as conn:
            judgment = repository.get(conn, "judgments", response.json()["judgment_id"])
            assert judgment["machine_status"] == "NOT_CHECKABLE" and judgment["run_id"] is None
        assert client.post(url, json={"single_predicate_confirmed": True}, headers=AUTH).status_code == 409


def test_factual_select_edit_and_executor(repository):
    seen = []
    def handler(run_id):
        seen.append((run_id, threading.current_thread().name))
    with TestClient(create_app(settings(), StubLLM({"default": claim_response()}), repository, run_handler=handler)) as client:
        submission = client.post("/submissions", json={"text": TEXT}, headers=AUTH).json()
        pid = submission["propositions"][0]["id"]
        url = f"/submissions/{submission['submission_id']}/propositions/{pid}/select"
        assert client.post(url, json={}, headers=AUTH).status_code == 400
        response = client.post(url, json={"single_predicate_confirmed": True, "edited_text": "Edited Philippine claim."}, headers=AUTH)
        assert response.status_code == 200 and "run_id" in response.json()
        with repository.transaction() as conn:
            prop = repository.get(conn, "propositions", pid)
            assert prop["text"] == "Edited Philippine claim." and prop["original_text"] == TEXT and prop["operator_edited"]
            assert repository.get(conn, "research_runs", response.json()["run_id"])["proposition_id"] == pid
    assert seen and seen[0][1].startswith("veryjudge")


def test_select_wrong_submission(repository):
    with TestClient(create_app(settings(), database=repository)) as client:
        assert client.post("/submissions/sub_missing/propositions/prop_missing/select", json={"single_predicate_confirmed": True}, headers=AUTH).status_code == 404
