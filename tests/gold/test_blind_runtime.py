"""Blind-label regression layer.

`RuntimeInput` deliberately excludes every gold oracle field.  It is passed to
the runner-side replay helper; only the final assertion receives an `Oracle`.
Prepared cassette tests in test_gold.py remain evidence-packet tests, not this
end-to-end blind layer.
"""
from dataclasses import dataclass
from typing import Optional

from veryjudge.explanation import ExplanationWriter
from veryjudge.fetcher import DocumentFetcher, RecordedFetcher
from veryjudge.licenser import license
from veryjudge.llm import StubLLM
from veryjudge.models import Document, Evidence, EvidenceCandidate, Locator, Proposition
from veryjudge.providers import RecordReplay
from veryjudge.validators import LocatorValidator


@dataclass(frozen=True)
class RuntimeInput:
    submitted_text: str
    source_url: str
    proposition_type: str
    claim_type: str
    time_anchor: str
    entities: tuple[str, ...]


@dataclass(frozen=True)
class Oracle:
    status: str
    route: Optional[str]


def run_blind(packet, tmp_path):
    """Replay -> extraction/classification -> licensing -> explanation.

    The only supplied facts are the claim annotations and replay source.  The
    candidate is generated from the replayed paragraph, never from an oracle.
    """
    prop = Proposition(id="blind_prop", submission_id="blind_sub", ordinal=1,
        text=packet.submitted_text, original_text=packet.submitted_text,
        text_normalized=packet.submitted_text.lower(), proposition_type=packet.proposition_type,
        type_reason="runtime annotation", claim_type=packet.claim_type,
        time_anchor=packet.time_anchor, entities=list(packet.entities))
    fetched = RecordedFetcher(DocumentFetcher(tmp_path / "captures"), RecordReplay("fixtures/cassettes")).fetch(
        packet.source_url, run_id="blind_run", document_id="blind_doc")
    doc = Document(**fetched.model_dump(), id="blind_doc", run_id="blind_run", operator_added=True)
    paragraph = next(p for p in doc.paragraphs if "109,035,343" in doc.text[p.start:p.end])
    excerpt = doc.text[paragraph.start:paragraph.end]
    candidate = EvidenceCandidate(paragraph_index=paragraph.index, excerpt=excerpt,
        source_class="primary_record", relationship="supports", strength="direct", rationale="replayed text")
    found = LocatorValidator.validate(doc, candidate, prop)
    assert found.ok
    evidence = Evidence(id="blind_ev", proposition_id=prop.id, run_id="blind_run", document_id=doc.id,
        locator=Locator(start=found.locator_start, end=found.locator_end, paragraph_index=paragraph.index),
        excerpt=excerpt, source_class="primary_record", relationship="supports", strength="direct",
        model_rationale="replayed text")
    result = license(prop, [evidence], documents=[doc])
    explanation = ExplanationWriter(StubLLM({"default": {"sentences": [], "what_would_change": [], "limitations": []}})).write(
        prop, result, [evidence], [doc])
    return result.status.value, result.route, explanation.model_dump(mode="json")


def test_blind_label_end_to_end_replay_and_wrong_label_mutation(tmp_path):
    packet = RuntimeInput("The 2020 census counted the Philippine population at 109,035,343.",
        "https://psa.gov.ph/system/files/phcd/2022-12/Press%2520Release%2520-%25202020%2520CPH%2520Population%2520Counts%2520Press%2520Release.pdf",
        "factual", "statistic", "2020", ("Philippine", "109,035,343"))
    actual = run_blind(packet, tmp_path / "one")
    mutated = run_blind(packet, tmp_path / "two")
    correct, wrong = Oracle("SUPPORTED", None), Oracle("CONTRADICTED", "A")
    assert actual == mutated
    assert actual[:2] == (correct.status, correct.route)
    assert actual[:2] != (wrong.status, wrong.route)
