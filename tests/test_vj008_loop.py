import time

from veryjudge.models import FetchResult, RunnerConfig, SearchResult
from veryjudge.runner import QueryPlanner, SingleAgentRunner, StubEvidenceExtractor
from tests.helpers import NOW, TEXT, candidate, proposition


class PlanningLLM:
    def __init__(self):
        self.index = 0

    def complete(self, *args):
        self.index += 1
        return dict(query=f"additional research query {self.index}", language="en", planner_reason="Continue looking for primary evidence")


class SearchStub:
    def __init__(self, count=1):
        self.index, self.count = 0, count

    def search(self, query, language, scope):
        self.index += 1
        return [SearchResult(url=f"https://example.test/{self.index}/{i}", title="fixture", snippet="fixture", provider="web", rank=i+1)
                for i in range(self.count)]


class FetchStub:
    def __init__(self, failures=0):
        self.calls, self.failures = [], failures

    def fetch(self, url, timeout_s=20, **context):
        self.calls.append(url)
        if len(self.calls) <= self.failures:
            return FetchResult(url=url, final_url=url, fetch_status="failed", failure_reason="http_404", fetched_at=NOW)
        return FetchResult(url=url, final_url=url, fetch_status="ok", text=TEXT,
                           paragraphs=[dict(index=0, start=0, end=len(TEXT))], fetched_at=NOW)


def runner(search=None, fetch=None, extractor=None):
    search = search or SearchStub()
    return SingleAgentRunner({"web": search, "curated_ph": search}, fetch or FetchStub(),
                             extractor or StubEvidenceExtractor(), QueryPlanner(PlanningLLM()))


def test_budget_enforced():
    trace = runner().run(proposition(), RunnerConfig())
    assert len(trace.steps) == 8 and len(trace.documents) <= 15
    assert sum(d.fetch_status == "ok" for d in trace.documents) <= 10
    assert trace.stop_reason == "budget_exhausted"


def test_failed_fetch_does_not_consume_capture_slot():
    trace = runner(SearchStub(3), FetchStub(5)).run(proposition(), RunnerConfig())
    assert len(trace.documents) == 15 and sum(d.fetch_status == "ok" for d in trace.documents) == 10


def test_min_searches_before_stop():
    extractor = StubEvidenceExtractor({"https://example.test/1/0": [candidate().model_dump()]})
    trace = runner(extractor=extractor).run(proposition(), RunnerConfig())
    assert len(trace.steps) >= 3 and "https://example.test/3/0" in extractor.calls
    assert trace.stop_reason == "sufficient_primary"


def test_captured_url_provenance():
    trace = runner().run(proposition(), RunnerConfig())
    by_step = {step.id: {r.url for r in step.results} for step in trace.steps}
    assert all(doc.url in by_step[doc.retrieval_step_id] for doc in trace.documents)


def test_runner_never_sets_status():
    trace = runner().run(proposition(), RunnerConfig())
    assert "status" not in trace.model_dump() and not hasattr(trace, "machine_status")


def test_link_discovered_url_allowed():
    class LinkedFetch(FetchStub):
        def fetch(self, url, *args, **kwargs):
            result = super().fetch(url, *args, **kwargs)
            if url == "https://example.test/1/0":
                body = TEXT + " https://psa.gov.ph/linked-record"
                result = result.model_copy(update={"text": body, "paragraphs": []})
            return result
    trace = runner(fetch=LinkedFetch()).run(proposition(), RunnerConfig())
    linked = next(doc for doc in trace.documents if doc.url == "https://psa.gov.ph/linked-record")
    source = next(doc for doc in trace.documents if doc.id == linked.discovered_from_document_id)
    assert linked.url in source.text and linked.run_id == source.run_id
    assert linked.retrieval_step_id is None


def test_dedupe_per_run():
    class AliasFetch(FetchStub):
        def fetch(self, url, *args, **kwargs):
            return super().fetch(url, *args, **kwargs).model_copy(update={"final_url": "https://example.test/canonical"})
    trace = runner(fetch=AliasFetch()).run(proposition(), RunnerConfig())
    assert sum(doc.fetch_status == "ok" for doc in trace.documents) == 1
    assert len({doc.url for doc in trace.documents}) == len(trace.documents)


def test_cooperative_timeout_preserves_evidence():
    class SlowFetch(FetchStub):
        def fetch(self, url, *args, **kwargs):
            if self.calls:
                time.sleep(0.06)
            return super().fetch(url, *args, **kwargs)
    extractor = StubEvidenceExtractor({"https://example.test/1/0": [candidate().model_dump()]})
    trace = runner(fetch=SlowFetch(), extractor=extractor).run(proposition(), RunnerConfig(wall_clock_s=0.04))
    assert trace.stop_reason == "timeout" and len(trace.evidence) == 1
