from veryjudge.explanation import ExplanationWriter, evidence_chips
from veryjudge.llm import StubLLM
from veryjudge.models import StatusResult
from tests.helpers import NOW, TEXT, document, evidence, proposition


def test_explanation_invalid_falls_back():
    llm=StubLLM({"default":dict(sentences=[dict(text=TEXT,kind="factual",evidence_ids=["ev_bogus"])],what_would_change=[],limitations=[])})
    exp=ExplanationWriter(llm).write(proposition(),StatusResult(status="SUPPORTED"),[evidence()],[document()])
    assert len(llm.calls)==2
    assert exp.sentences[0].text=="SUPPORTED. See the evidence table." and exp.sentences[0].kind=="meta"


def test_writer_sentence_kind_required_ids():
    llm=StubLLM({"default":dict(sentences=[dict(text=TEXT,kind="factual",evidence_ids=["ev_test"])],what_would_change=[],limitations=[])})
    exp=ExplanationWriter(llm).write(proposition(),StatusResult(status="SUPPORTED"),[evidence()],[document()])
    assert exp.sentences[0].evidence_ids==["ev_test"] and len(llm.calls)==1


def test_explanation_shows_published_at():
    assert evidence_chips([evidence()],[document()])[0]["published_at"]=="undated"
    assert evidence_chips([evidence()],[document(published_at=NOW)])[0]["published_at"]==NOW.isoformat()


def test_empty_evidence_template_has_trace_limitations():
    llm=StubLLM()
    exp=ExplanationWriter(llm).write(proposition(),StatusResult(status="NOT_ESTABLISHED"),[],[],[1,2,3])
    assert not llm.calls and exp.limitations==["3 searches, 0 usable sources"]


def test_missing_kind_regenerates_once_then_template():
    llm=StubLLM({"default":dict(sentences=[dict(text=TEXT,evidence_ids=["ev_test"])],what_would_change=[],limitations=[])})
    exp=ExplanationWriter(llm).write(proposition(),StatusResult(status="SUPPORTED"),[evidence()],[document()])
    assert len(llm.calls)==2 and exp.sentences[0].kind=="meta"
