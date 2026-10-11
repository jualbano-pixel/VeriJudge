from datetime import datetime, timezone, timedelta
from threading import Event
import time

import pytest
from sqlalchemy.exc import IntegrityError

from veryjudge.db import row_from_object
from veryjudge.executor import RunExecutor, reap_orphaned_runs
from veryjudge.ids import new_id
from veryjudge.research import ResearchService
from veryjudge.runner import SingleAgentRunner, QueryPlanner, StubEvidenceExtractor
from tests.helpers import TEXT, candidate
from tests.test_vj006 import repository
from tests.test_vj008_loop import FetchStub, SearchStub, PlanningLLM


def create_run(db, wall=180, started_at=None):
    sid, pid, rid = new_id("sub"), new_id("prop"), new_id("run")
    with db.transaction() as conn:
        db.insert(conn, "submissions", dict(id=sid, text=TEXT, text_sha256="fixture", extraction_status="ok"))
        db.insert(conn, "propositions", dict(id=pid, submission_id=sid, ordinal=1, text=TEXT, original_text=TEXT,
                  text_normalized=TEXT.lower(), proposition_type="factual", type_reason="fixture", claim_type="statistic",
                  time_anchor="2020", entities=["Philippine"]))
        db.insert(conn, "research_runs", dict(id=rid, proposition_id=pid, runner_version="0.1.0", model="stub",
                  status="running", started_at=started_at or datetime.now(timezone.utc),
                  config_json=dict(max_searches=8,max_captures=10,max_fetch_attempts=15,wall_clock_s=wall)))
    return rid


def read_run(db, rid):
    with db.transaction() as conn:
        return dict(db.get(conn,"research_runs",rid))


def make_service(db, fetcher=None, extractor=None):
    def factory(event):
        search = SearchStub()
        return SingleAgentRunner({"web":search,"curated_ph":search},fetcher or FetchStub(),
            extractor or StubEvidenceExtractor(),QueryPlanner(PlanningLLM()),event=event)
    return ResearchService(db,factory)


def test_executor_is_threadpool(repository):
    rid = create_run(repository)
    service = make_service(repository)
    executor = RunExecutor(repository,service.execute,on_timeout=service.timeout)
    assert executor.pool._max_workers == 2
    executor.submit(rid).result(timeout=5)
    executor.shutdown()
    assert read_run(repository,rid)["status"] == "done"
    with repository.transaction() as conn:
        steps = repository.rows(conn,"retrieval_steps",run_id=rid)
        documents = repository.rows(conn,"documents",run_id=rid)
        assert len(steps)==8 and len(documents)==8
        assert all(step["result_count"]==1 for step in steps)


def test_orphaned_run_reaped(repository):
    rid = create_run(repository,started_at=datetime.now(timezone.utc)-timedelta(seconds=181))
    reap_orphaned_runs(repository)
    row = read_run(repository,rid)
    assert row["status"] == "failed" and row["failure_reason"] == "orphaned" and row["finished_at"]


def test_timeout_preserves_evidence(repository):
    entered, release = Event(), Event()
    class BlockingFetch(FetchStub):
        def fetch(self,*args,**kwargs):
            if self.calls:
                entered.set()
                release.wait(3)
            return super().fetch(*args,**kwargs)
    rid = create_run(repository,wall=0.5)
    service = make_service(repository,BlockingFetch(),StubEvidenceExtractor({"https://example.test/1/0":[candidate().model_dump()]}))
    executor = RunExecutor(repository,service.execute,on_timeout=service.timeout)
    future = executor.submit(rid)
    try:
        assert entered.wait(1)
        limit = time.monotonic()+1.5
        while read_run(repository,rid)["status"] == "running" and time.monotonic()<limit:
            time.sleep(0.02)
        assert read_run(repository,rid)["status"] == "timeout"
        assert not future.done()  # watchdog finalized without waiting for blocked fetch
        with repository.transaction() as conn:
            before = repository.rows(conn,"evidence",run_id=rid)
            docs = repository.rows(conn,"documents",run_id=rid)
            assert len(before)==1 and len(docs)==2 and sum(d["fetch_status"]=="failed" for d in docs)==1
    finally:
        release.set()
        future.result(timeout=3)
        executor.shutdown()
    with repository.transaction() as conn:
        assert repository.rows(conn,"evidence",run_id=rid)==before
    assert read_run(repository,rid)["status"]=="timeout"


def test_run_input_and_terminal_freeze(repository):
    rid = create_run(repository)
    table = repository.tables["research_runs"]
    with pytest.raises(IntegrityError):
        with repository.transaction() as conn:
            conn.execute(table.update().where(table.c.id==rid).values(model="changed"))
    with repository.transaction() as conn:
        conn.execute(table.update().where(table.c.id==rid).values(token_in=12))
        conn.execute(table.update().where(table.c.id==rid).values(status="done",finished_at=datetime.now(timezone.utc)))
    with pytest.raises(IntegrityError):
        with repository.transaction() as conn:
            conn.execute(table.update().where(table.c.id==rid).values(token_in=13))


def test_step_finalizes_exactly_once_even_empty(repository):
    rid = create_run(repository)
    step = repository.tables["retrieval_steps"]
    sid = new_id("step")
    with repository.transaction() as conn:
        repository.insert(conn,"retrieval_steps",dict(id=sid,run_id=rid,step_number=1,provider="web",query="query",
            language="en",results_json=[],result_count=-1,planner_reason="fixture"))
    with pytest.raises(IntegrityError):
        with repository.transaction() as conn:
            conn.execute(step.update().where(step.c.id==sid).values(query="rewrite",result_count=0))
    with repository.transaction() as conn:
        conn.execute(step.update().where(step.c.id==sid).values(results_json=[],result_count=0))
    with pytest.raises(IntegrityError):
        with repository.transaction() as conn:
            conn.execute(step.update().where(step.c.id==sid).values(results_json=[],result_count=0))


def test_step_persisted_before_provider(repository):
    rid = create_run(repository)
    class AssertingSearch(SearchStub):
        def search(self,query,language,scope):
            with repository.transaction() as conn:
                steps=repository.rows(conn,"retrieval_steps",run_id=rid)
                assert any(s["query"]==query and s["result_count"]==-1 for s in steps)
            return super().search(query,language,scope)
    def factory(event):
        search=AssertingSearch()
        return SingleAgentRunner({"web":search,"curated_ph":search},FetchStub(),StubEvidenceExtractor(),QueryPlanner(PlanningLLM()),event=event)
    service=ResearchService(repository,factory)
    executor=RunExecutor(repository,service.execute,on_timeout=service.timeout)
    executor.submit(rid).result(timeout=5)
    executor.shutdown()
    assert read_run(repository,rid)["status"]=="done"


def test_judgment_run_unique(repository):
    rid=create_run(repository)
    row=read_run(repository,rid)
    values=dict(proposition_id=row["proposition_id"],run_id=rid,machine_status="NOT_ESTABLISHED",
                machine_explanation_json={"sentences":[],"what_would_change":[],"limitations":[]},
                as_of=datetime.now(timezone.utc))
    with repository.transaction() as conn:
        repository.insert(conn,"judgments",dict(id=new_id("jdg"),**values))
    with pytest.raises(IntegrityError):
        with repository.transaction() as conn:
            repository.insert(conn,"judgments",dict(id=new_id("jdg"),**values))
