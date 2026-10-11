"""Configured single-runner factory; all provider traffic uses record/replay."""
import os
from pathlib import Path

from veryjudge.fetcher import DocumentFetcher, RecordedFetcher
from veryjudge.providers import CuratedPHProvider, RecordReplay, RecordedSearchProvider, WebSearchProvider
from veryjudge.runner import QueryPlanner, SingleAgentRunner, StubEvidenceExtractor
from veryjudge.evidence_extractor import EvidenceExtractor


def default_runner_factory(settings, llm):
    def factory(event):
        serverless = bool(os.environ.get("VERCEL"))
        runtime_root = Path(os.environ.get("VJ_RUNTIME_DIR", "/tmp/veryjudge" if serverless else "."))
        cassette_dir = runtime_root / "cassettes" if serverless else Path("fixtures/cassettes")
        recorder = RecordReplay(cassette_dir, os.environ.get("VJ_CASSETTE_MODE", "record" if serverless else "replay"))
        web = WebSearchProvider(settings.provider_keys.get("web", ""))
        providers = {"web": RecordedSearchProvider(web, recorder),
                     "curated_ph": RecordedSearchProvider(CuratedPHProvider(web, "psa"), recorder)}
        capture_dir = runtime_root / "captures" if serverless else Path("captures")
        log_dir = runtime_root / "run_logs" if serverless else Path("run_logs")
        return SingleAgentRunner(providers, RecordedFetcher(DocumentFetcher(capture_dir), recorder),
                                 EvidenceExtractor(llm), QueryPlanner(llm), model=settings.model,
                                 event=event, log_directory=log_dir)
    return factory
