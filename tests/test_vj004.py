import httpx
import pytest

from veryjudge.models import RetrievalStep, SearchResult, SearchScope
from veryjudge.providers import CuratedPHProvider, ProviderError, RecordReplay, RecordedSearchProvider, WebSearchProvider


class StubSearch:
    name = "web"
    calls = None

    def search(self, query, language, scope):
        self.calls = (query, language, scope)
        return [SearchResult(url="https://psa.gov.ph/example", title="fixture", snippet="census", provider="web", rank=1)]


def test_cassette_replay(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    stub = StubSearch()
    recorded = RecordedSearchProvider(stub, RecordReplay(tmp_path, "record"))
    expected = recorded.search("2020 census", "en", SearchScope())
    monkeypatch.setattr(stub, "search", lambda *args: pytest.fail("live call during replay"))
    monkeypatch.setenv("CI", "true")
    replay = RecordedSearchProvider(stub, RecordReplay(tmp_path))
    assert replay.search("2020 census", "en", SearchScope()) == expected
    with pytest.raises(ProviderError, match="missing_cassette"):
        replay.search("unrecorded", "en", SearchScope())


def test_ci_mode_live_call_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CI", "true")
    with pytest.raises(ProviderError, match="forbidden"):
        RecordReplay(tmp_path, "record").call("search", {}, lambda: pytest.fail("live"))
    with pytest.raises(ProviderError, match="forbidden"):
        WebSearchProvider("test").search("query", "en", SearchScope())


def test_curated_scope_and_provider():
    stub = StubSearch()
    results = CuratedPHProvider(stub, "psa").search("census", "en", SearchScope())
    assert stub.calls[2].site_restrict == ["psa.gov.ph", "openstat.psa.gov.ph"]
    assert results[0].provider == "curated_ph"


def test_retrieval_step_records_query_provider():
    step = RetrievalStep(id="step_test", run_id="run_test", step_number=1, provider="curated_ph", record_key="psa",
                         query="census", language="en", site_restrict=["psa.gov.ph"],
                         results=StubSearch().search("census", "en", SearchScope()), result_count=1,
                         planner_reason="statistic claim → curated PSA first")
    assert step.query and step.provider and step.language and step.planner_reason and step.results


def test_provider_error_replayed(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    def failing():
        raise ProviderError("outage", True)
    with pytest.raises(ProviderError):
        RecordReplay(tmp_path, "record").call("search", {}, failing)
    with pytest.raises(ProviderError) as error:
        RecordReplay(tmp_path).call("search", {}, lambda: pytest.fail("network"))
    assert error.value.retryable


def test_web_adapter_mapping(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    def handle(request):
        assert "site:psa.gov.ph" in request.url.params["q"]
        return httpx.Response(200, json={"web": {"results": [{"url": "https://psa.gov.ph", "title": "PSA", "description": "fixture"}]}})
    provider = WebSearchProvider("fake-key", httpx.Client(transport=httpx.MockTransport(handle)))
    rows = provider.search("census", "en", SearchScope(site_restrict=["psa.gov.ph"]))
    assert rows[0].provider == "web" and rows[0].rank == 1
