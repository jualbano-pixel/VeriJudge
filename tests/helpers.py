from datetime import datetime, timezone

from veryjudge.models import Document, EvidenceCandidate, Evidence, Proposition
from veryjudge.validators import LocatorValidator

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
TEXT = "The Philippine population was 109,035,343 in the 2020 census."


def proposition(**changes):
    data = dict(id="prop_test", submission_id="sub_test", ordinal=1, text=TEXT,
                original_text=TEXT, text_normalized=TEXT.lower(), proposition_type="factual",
                type_reason="A census count", claim_type="statistic", time_anchor="2020",
                entities=["Philippine", "109,035,343"])
    return Proposition(**{**data, **changes})


def document(text=TEXT, **changes):
    data = dict(id="doc_test", run_id="run_test", retrieval_step_id="step_test",
                url="https://example.test/census", fetch_status="ok", text=text,
                paragraphs=[dict(index=0, start=0, end=len(text))] if text is not None else [], fetched_at=NOW)
    return Document(**{**data, **changes})


def candidate(**changes):
    data = dict(paragraph_index=0, excerpt=TEXT, source_class="primary_record",
                relationship="supports", strength="direct", rationale="fixture")
    return EvidenceCandidate(**{**data, **changes})


def evidence(doc=None, prop=None, **changes):
    doc, prop = doc or document(), prop or proposition()
    cand = candidate(**{k: v for k, v in changes.items() if k in EvidenceCandidate.model_fields})
    locator = LocatorValidator.validate(doc, cand, prop)
    fields = cand.model_dump(exclude={"paragraph_index", "rationale"})
    data = dict(id="ev_test", proposition_id=prop.id, run_id=doc.run_id, document_id=doc.id,
                locator=dict(start=locator.locator_start, end=locator.locator_end, paragraph_index=cand.paragraph_index),
                model_rationale=cand.rationale, **fields)
    return Evidence(**{**data, **{k: v for k, v in changes.items() if k in Evidence.model_fields}})
