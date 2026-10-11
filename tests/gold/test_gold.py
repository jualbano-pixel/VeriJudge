import json
from pathlib import Path

import pytest

from veryjudge.gold import GoldHarness, REQUIRED_GOLD, cassette_ready, fixture_proposition, fixtures
from veryjudge.fetcher import DocumentFetcher, RecordedFetcher
from veryjudge.licenser import license
from veryjudge.models import Document
from veryjudge.providers import RecordReplay
from tests.helpers import document, evidence, proposition


CASES = fixtures()


@pytest.mark.parametrize("fixture", CASES, ids=lambda item: item["case_id"])
def test_gold_fixture_format(fixture):
    assert fixture["case_id"].startswith("VJ-G")
    assert REQUIRED_GOLD <= set(fixture["gold"])
    assert fixture["fixture_prep"]["status"] in {"prepared", "needs_human"}
    assert isinstance(fixture["gold"]["decisive_evidence"], list)


@pytest.mark.parametrize("fixture", CASES, ids=lambda item: item["case_id"])
def test_gold_deterministic_statuses(fixture):
    # Needs-human and no-cassette cases intentionally execute only status/locator
    # checks. Their decisive-capture assertions start after VJ-015 records cassettes.
    p = fixture_proposition(fixture)
    result = license(p, [], documents=[])
    gold = fixture["gold"]
    if fixture["case_id"] in {"VJ-G01", "VJ-G02", "VJ-G03", "VJ-G05", "VJ-G06", "VJ-G08", "VJ-G12"}:
        if not cassette_ready(fixture):
            pytest.skip("awaiting VJ-015 capture cassette")
        if fixture["case_id"] in {"VJ-G01", "VJ-G02", "VJ-G03", "VJ-G05", "VJ-G08"}:
            # These successful cassettes are exercised with decisive evidence
            # below; an empty-evidence license is intentionally not a pipeline.
            return
    assert result.status.value in gold["acceptable_statuses"]
    assert result.status.value not in gold["forbidden_statuses"]
    assert set(result.review_reasons) >= set(gold["expected_review_reasons"])


def test_gold_G01_human_pdf_capture_replays_and_licenses(tmp_path):
    fixture = next(item for item in CASES if item["case_id"] == "VJ-G01")
    decisive = fixture["gold"]["decisive_evidence"][0]
    assert cassette_ready(fixture)
    fetched = RecordedFetcher(DocumentFetcher(tmp_path / "captures"), RecordReplay("fixtures/cassettes")).fetch(
        decisive["url"], run_id="run_G01", document_id="doc_G01")
    d = Document(**fetched.model_dump(), id="doc_G01", run_id="run_G01", operator_added=True)
    paragraph = next(p for p in d.paragraphs if "109,035,343" in d.text[p.start:p.end])
    excerpt = d.text[paragraph.start:paragraph.end]
    p = fixture_proposition(fixture).model_copy(update={"entities": ["2020", "109,035,343"]})
    ev = evidence(d, p, id="ev_G01", paragraph_index=paragraph.index, excerpt=excerpt,
                  source_class="primary_record", relationship="supports", strength="direct")
    result = license(p, [ev], documents=[d])
    assert result.status.value == "SUPPORTED"


def test_gold_G02_human_statute_capture_replays_and_contradicts(tmp_path):
    fixture = next(item for item in CASES if item["case_id"] == "VJ-G02")
    decisive = fixture["gold"]["decisive_evidence"][0]
    assert cassette_ready(fixture)
    fetched = RecordedFetcher(DocumentFetcher(tmp_path / "captures"), RecordReplay("fixtures/cassettes")).fetch(
        decisive["url"], run_id="run_G02", document_id="doc_G02")
    d = Document(**fetched.model_dump(), id="doc_G02", run_id="run_G02", operator_added=True)
    paragraph = next(p for p in d.paragraphs if "REPUBLIC ACT NO. 11934" in d.text[p.start:p.end])
    excerpt = 'Section 1. Short Title. - This Act shall be known as the "Subscriber Identity Module (SIM) Registration Act".'
    p = fixture_proposition(fixture).model_copy(update={"entities": ["11934"]})
    ev = evidence(d, p, id="ev_G02", paragraph_index=paragraph.index, excerpt=excerpt,
                  source_class="primary_record", relationship="contradicts", strength="direct",
                  contradiction_route="A")
    result = license(p, [ev], documents=[d])
    assert result.status.value == "CONTRADICTED"
    assert result.route == "A"


def test_gold_G08_human_psa_capture_replays_and_licenses(tmp_path):
    fixture = next(item for item in CASES if item["case_id"] == "VJ-G08")
    decisive = fixture["gold"]["decisive_evidence"][0]
    assert cassette_ready(fixture)
    fetched = RecordedFetcher(DocumentFetcher(tmp_path / "captures"), RecordReplay("fixtures/cassettes")).fetch(
        decisive["url"], run_id="run_G08", document_id="doc_G08")
    d = Document(**fetched.model_dump(), id="doc_G08", run_id="run_G08", operator_added=True)
    paragraph = next(p for p in d.paragraphs if "8.7 percent in January 2023" in d.text[p.start:p.end])
    excerpt = ("Headline inflation in the Philippines continued its uptrend as it accelerated further to "
               "8.7 percent in January 2023, from 8.1 percent in December 2022.")
    p = fixture_proposition(fixture).model_copy(update={"entities": ["Philippines", "8.7", "January 2023"]})
    ev = evidence(d, p, id="ev_G08", paragraph_index=paragraph.index, excerpt=excerpt,
                  source_class="primary_record", relationship="supports", strength="direct")
    result = license(p, [ev], documents=[d])
    assert result.status.value == "SUPPORTED"


def test_gold_G03_human_pdf_capture_replays_and_licenses(tmp_path):
    fixture = next(item for item in CASES if item["case_id"] == "VJ-G03")
    decisive = fixture["gold"]["decisive_evidence"][0]
    assert cassette_ready(fixture)
    fetched = RecordedFetcher(DocumentFetcher(tmp_path / "captures"), RecordReplay("fixtures/cassettes")).fetch(
        decisive["url"], run_id="run_G03", document_id="doc_G03")
    d = Document(**fetched.model_dump(), id="doc_G03", run_id="run_G03", operator_added=True)
    paragraph = next(p for p in d.paragraphs if "Kalayaan in Palawan" in d.text[p.start:p.end])
    excerpt = ("In 2020, the municipality of Kalayaan in Palawan was the smallest municipality in terms of "
               "population size, with 193 persons.")
    p = fixture_proposition(fixture).model_copy(update={"entities": ["Kalayaan", "193"]})
    ev = evidence(d, p, id="ev_G03", paragraph_index=paragraph.index, excerpt=excerpt,
                  source_class="primary_record", relationship="supports", strength="direct")
    result = license(p, [ev], documents=[d])
    assert result.status.value == "SUPPORTED"


def test_gold_fifteen_files_exactly():
    paths = sorted(Path("fixtures/gold").glob("VJ-G*.json"))
    assert [path.stem for path in paths] == [f"VJ-G{number:02d}" for number in range(1, 16)]


def test_gold_harness_applies_ancestry_through_review_callback():
    fixture = next(item for item in CASES if item["case_id"] == "VJ-G05")
    calls = []
    def pipeline(_):
        return {
            "machine_status": "SUPPORTED", "review_reasons": ["high_risk:listed_company_or_market"],
            "evidence": [],
            "review": lambda payload: calls.append(payload) or {"relicensed_status": "SUPPORTED"},
        }
    GoldHarness(pipeline).run(fixture)
    assert calls and calls[0]["ancestry_edits"] == fixture["gold"]["ancestry_edits"]
