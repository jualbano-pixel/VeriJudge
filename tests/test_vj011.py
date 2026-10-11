import time
from datetime import datetime,timezone

from fastapi.testclient import TestClient

from veryjudge.app import create_app
from veryjudge.llm import StubLLM
from veryjudge.judgments import JudgmentService
from tests.test_vj006 import AUTH,repository,settings
from tests.test_vj008_lifecycle import create_run,read_run

VIEWER={"Authorization":"Bearer test-viewer"}


def terminal(db,rid,status="timeout"):
    with db.transaction() as conn:
        table=db.tables["research_runs"]
        conn.execute(table.update().where(table.c.id==rid).values(status=status,finished_at=datetime.now(timezone.utc),stop_reason="timeout" if status=="timeout" else "budget_exhausted"))


def await_judgment(client,rid):
    for _ in range(100):
        result=client.get(f"/runs/{rid}",headers=AUTH).json()
        if result.get("judgment_id"):
            return result["judgment_id"]
        time.sleep(0.02)
    raise AssertionError(result)


def test_retry_supersedes(repository):
    rid=create_run(repository)
    terminal(repository,rid)
    service=JudgmentService(repository,StubLLM())
    prior=service.complete(rid)
    with TestClient(create_app(settings(),database=repository,run_handler=lambda r:None)) as client:
        response=client.post(f"/runs/{rid}/retry",headers=AUTH)
        assert response.status_code==200
        new_id=response.json()["run_id"]
        assert new_id!=rid
        new_judgment=await_judgment(client,new_id)
        assert client.get(f"/judgments/{prior}",headers=AUTH).json()["superseded_by"]==new_judgment


def test_retry_only_failed_or_timeout(repository):
    rid=create_run(repository)
    terminal(repository,rid,"done")
    with TestClient(create_app(settings(),database=repository)) as client:
        assert client.post(f"/runs/{rid}/retry",headers=AUTH).status_code==409


def test_draft_not_exposed(repository):
    rid=create_run(repository)
    terminal(repository,rid)
    jid=JudgmentService(repository,StubLLM()).complete(rid)
    with TestClient(create_app(settings(),database=repository)) as client:
        assert client.get(f"/judgments/{jid}",headers=VIEWER).status_code==404
        with repository.transaction() as conn:
            table=repository.tables["judgments"]
            conn.execute(table.update().where(table.c.id==jid).values(review_state="approved",final_status="NOT_ESTABLISHED"))
        response=client.get(f"/judgments/{jid}",headers=VIEWER)
        assert response.status_code==200 and not ({"reviews","rule_trace","trace"}&response.json().keys())
        assert client.post(f"/judgments/{jid}/review",json={"decision":"approve"},headers=VIEWER).status_code in {403,404}
        assert client.get(f"/runs/{rid}",headers=VIEWER).status_code==404


def test_licenser_scoped_to_run(repository,monkeypatch):
    rid=create_run(repository)
    terminal(repository,rid)
    from veryjudge import judgments
    original=judgments.license
    seen=[]
    def checking(p,e,*args,**kwargs):
        assert all(row.run_id==rid and not row.removed_by_operator for row in e)
        seen.append(True)
        return original(p,e,*args,**kwargs)
    monkeypatch.setattr(judgments,"license",checking)
    assert JudgmentService(repository,StubLLM()).complete(rid)
    assert seen


def test_full_stubbed_pipeline_produces_judgment(repository):
    from tests.test_vj006 import claim_response
    from tests.test_vj008_loop import runner
    from tests.helpers import TEXT,candidate
    from veryjudge.runner import StubEvidenceExtractor
    def factory(event):
        r=runner(extractor=StubEvidenceExtractor({"https://example.test/1/0":[candidate().model_dump()]}))
        r.event=event
        return r
    llm=StubLLM({"default":claim_response(high_risk_category=None)})
    with TestClient(create_app(settings(),llm,repository,runner_factory=factory)) as client:
        sub=client.post("/submissions",json={"text":TEXT},headers=AUTH).json()
        pid=sub["propositions"][0]["id"]
        run=client.post(f"/submissions/{sub['submission_id']}/propositions/{pid}/select",json={"single_predicate_confirmed":True},headers=AUTH).json()
        jid=await_judgment(client,run["run_id"])
        result=client.get(f"/judgments/{jid}",headers=AUTH).json()
        assert result["machine_status"]=="SUPPORTED" and len(result["evidence"])==1
        assert result["evidence"][0]["run_id"]==run["run_id"]
