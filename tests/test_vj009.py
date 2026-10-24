import pytest

from tests.helpers import TEXT, candidate, document, proposition
from tests.test_vj008_loop import runner
from veryjudge.models import RunnerConfig
from veryjudge.runner import StubEvidenceExtractor


def test_rejected_candidates_counted(tmp_path):
    bad=candidate(excerpt="This is a paraphrase that does not occur in the record.")
    extractor=StubEvidenceExtractor({"https://example.test/1/0":[bad.model_dump()]})
    r=runner(extractor=extractor)
    r.log_directory=tmp_path
    trace=r.run(proposition(),RunnerConfig())
    assert trace.rejected_evidence_count==1 and not trace.evidence
    assert len(extractor.retry_calls)==1
    assert "paraphrase" in next(tmp_path.glob("*.jsonl")).read_text()


def test_verbatim_retry_recovers(tmp_path):
    extractor=StubEvidenceExtractor({"https://example.test/1/0":[candidate(excerpt="This is a paraphrased record that is not exact.").model_dump()]},
                                    {"https://example.test/1/0":TEXT})
    r=runner(extractor=extractor)
    r.log_directory=tmp_path
    trace=r.run(proposition(),RunnerConfig())
    assert len(trace.evidence)==1 and trace.rejected_evidence_count==0 and len(extractor.retry_calls)==1


@pytest.mark.parametrize("fields",[dict(source_class="official_statement"),dict(source_class="original_reporting"),
    dict(relationship="contradicts"),dict(relationship="contradicts",contradiction_route="B"),
    dict(relationship="contradicts",contradiction_route="C")])
def test_class_specific_fields_required_runner(tmp_path,fields):
    extractor=StubEvidenceExtractor({"https://example.test/1/0":[candidate(**fields).model_dump()]})
    r=runner(extractor=extractor)
    r.log_directory=tmp_path
    trace=r.run(proposition(),RunnerConfig())
    assert trace.rejected_evidence_count==1 and not trace.evidence and not extractor.retry_calls

# ── Requote schema tests (EvidenceExtractor + StubLLM — tests live requote code path) ──

import inspect
from veryjudge.evidence_extractor import EvidenceExtractor
from veryjudge.llm import StubLLM


def test_requote_accepts_valid_excerpt():
    """EvidenceExtractor.requote with a valid excerpt returns it."""
    llm = StubLLM({"default": {"excerpt": "The Philippine population was 109,035,343 in the 2020 census."}})
    extractor = EvidenceExtractor(llm)
    doc = document(text="The Philippine population was 109,035,343 in the 2020 census.")
    result = extractor.requote(proposition(), doc, candidate())
    assert result.excerpt == "The Philippine population was 109,035,343 in the 2020 census."


def test_requote_rejects_extra_type_field():
    """EvidenceExtractor.requote rejects responses that echo 'type': 'object' (VJ-016 regression)."""
    llm = StubLLM({"default": {"type": "object", "excerpt": "The Philippine population was 109,035,343 in the 2020 census."}})
    extractor = EvidenceExtractor(llm)
    with pytest.raises(Exception):
        extractor.requote(proposition(), document(), candidate())


def test_requote_rejects_missing_excerpt():
    """EvidenceExtractor.requote rejects responses without the required 'excerpt' field."""
    llm = StubLLM({"default": {"other": "field"}})
    extractor = EvidenceExtractor(llm)
    with pytest.raises(Exception):
        extractor.requote(proposition(), document(), candidate())


def test_requote_schema_has_title_and_strict_validation():
    """Inline requote schema includes title, required excerpt, and additionalProperties: False."""
    source = inspect.getsource(EvidenceExtractor.requote)
    assert '"title"' in source, "schema missing 'title' field"
    assert '"excerpt"' in source, "schema missing excerpt property"
    assert '"additionalProperties"' in source, "schema missing additionalProperties"


def test_strict_false_is_intentional():
    """Confirm strict=False is set on all LLM complete() calls — intentional design."""
    with open("veryjudge/llm.py") as f:
        llm_source = f.read()
    assert '"strict": False' in llm_source, "strict=False must remain set"
