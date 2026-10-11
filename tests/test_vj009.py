import pytest

from tests.helpers import TEXT, candidate, proposition
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
