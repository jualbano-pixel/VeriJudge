from datetime import date
import os
import subprocess
import sys

from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from veryjudge.anchors import parse_anchor, covers
from veryjudge.models import Explanation, SourceClass, Status
from veryjudge.validators import LocatorValidator, validate_evidence, validate_explanation
from tests.helpers import TEXT, candidate, document, evidence, proposition


@pytest.mark.parametrize("anchor,start,end", [
    ("2020", date(2020, 1, 1), date(2020, 12, 31)),
    ("2023-01", date(2023, 1, 1), date(2023, 1, 31)),
    ("2024-02", date(2024, 2, 1), date(2024, 2, 29)),
    ("2020-01-02", date(2020, 1, 2), date(2020, 1, 2)),
    ("2026-01-01/2026-06-30", date(2026, 1, 1), date(2026, 6, 30)),
])
def test_anchor_parse(anchor, start, end):
    assert parse_anchor(anchor) == (start, end)


@pytest.mark.parametrize("anchor", [None, "next month", "Jan 2023", "2023-02-29", "2020-13", "0000", "2020-1", "2020 ", "2020/2021", "2020-02-01/2020-01-01"])
def test_invalid_anchor(anchor):
    assert parse_anchor(anchor) is None
    assert proposition(time_anchor=anchor).time_anchor is None


def test_covers():
    assert covers("2020", "2020-02")
    assert not covers("2020-02", "2020")
    assert not covers(None, "2020")


def test_no_evidence_without_locator():
    ev = evidence().model_copy(update={"excerpt": "This paraphrase is not in the original text."})
    with pytest.raises(ValueError):
        validate_evidence(ev, document(), proposition(), "prop_test")


def test_locator_offsets_against_stored_text():
    d = document(text="Prefix. " + TEXT, paragraphs=[dict(index=0, start=0, end=7), dict(index=1, start=8, end=len(TEXT)+8)])
    result = LocatorValidator.validate(d, candidate(paragraph_index=1), proposition())
    assert result.ok and result.locator_start == 8 and d.text[result.locator_start:result.locator_end] == TEXT
    with pytest.raises(ValidationError):
        document(text="Unnormalized   stored text")


def test_locator_within_stated_paragraph_only():
    d = document(text="First paragraph. " + TEXT,
                 paragraphs=[dict(index=0, start=0, end=16), dict(index=1, start=17, end=17+len(TEXT))])
    assert LocatorValidator.validate(d, candidate(), proposition()).reason == "not_found"
    assert LocatorValidator.validate(d, candidate(paragraph_index=1), proposition()).ok
    assert LocatorValidator.validate(d, candidate(paragraph_index=2), proposition()).reason == "paragraph_missing"


@pytest.mark.parametrize("length,accepted", [(24, False), (25, True), (300, True), (301, False)])
def test_excerpt_min_length(length, accepted):
    value = "Philippine " + "x" * (length - 11)
    assert LocatorValidator.validate(document(text=value), candidate(excerpt=value), proposition()).ok == accepted


def test_excerpt_entity_overlap():
    numeric = "12345678901234567890123456789"
    assert not LocatorValidator.validate(document(text=numeric), candidate(excerpt=numeric), proposition()).ok
    assert LocatorValidator.validate(document(text="PHILIPPINE " + numeric), candidate(excerpt=numeric), proposition()).ok
    assert not LocatorValidator.validate(document(), candidate(), proposition(entities=[""])).ok


def test_no_evidence_on_failed_document():
    d = document(fetch_status="failed", text=None, paragraphs=[])
    assert LocatorValidator.validate(d, candidate(), proposition()).reason == "failed_document"


@pytest.mark.parametrize("fields", [
    dict(source_class="official_statement"), dict(source_class="original_reporting"),
    dict(relationship="contradicts"), dict(relationship="contradicts", contradiction_route="B"),
    dict(relationship="contradicts", contradiction_route="C", closure_period="2020"),
    dict(relationship="contradicts", contradiction_route="C", closure_record_key="TEST_ONLY_REGISTER"),
    dict(relationship="contradicts", contradiction_route="C", closure_record_key="TEST_ONLY_REGISTER", closure_period="next month"),
])
def test_class_specific_fields_required(fields):
    assert not LocatorValidator.validate(document(), candidate(**fields), proposition()).ok


def test_official_false_is_present():
    assert LocatorValidator.validate(document(), candidate(source_class="official_statement", attests_own_act=False), proposition()).ok


def test_first_occurrence():
    result = LocatorValidator.validate(document(text=TEXT + " " + TEXT), candidate(), proposition())
    assert result.locator_start == 0


@pytest.mark.parametrize("sentence", [
    dict(text="Unsupported assertion", evidence_ids=[]),
    dict(text="Unsupported assertion", kind="factual", evidence_ids=[]),
    dict(text="Bad id", kind="factual", evidence_ids=["ev_missing"]),
    dict(text="Meta citation", kind="meta", evidence_ids=["ev_test"]),
    dict(text="Bad kind", kind="summary", evidence_ids=[]),
])
def test_writer_sentence_kind_required_ids(sentence):
    with pytest.raises(ValueError):
        validate_explanation(dict(sentences=[sentence], what_would_change=[], limitations=[]), [evidence()])


def test_valid_explanation_and_removed_evidence():
    exp = dict(sentences=[dict(text=TEXT, kind="factual", evidence_ids=["ev_test"])], what_would_change=[], limitations=[])
    assert isinstance(validate_explanation(exp, [evidence()]), Explanation)
    with pytest.raises(ValueError):
        validate_explanation(exp, [evidence(removed_by_operator=True)])


def test_frozen_enums():
    assert len(Status) == 6 and len(SourceClass) == 9
    with pytest.raises(ValidationError):
        candidate(source_class="trusted")


@pytest.mark.postgres
def test_db_rejects_mismatched_locator():
    url = os.environ["TEST_DATABASE_URL"]
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env={**os.environ, "DATABASE_URL": url})
    engine = create_engine(url)
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            conn.execute(text("INSERT INTO submissions (id,text,text_sha256,extraction_status) VALUES ('sub_locator','claim','hash','ok')"))
            conn.execute(text("""INSERT INTO propositions (id,submission_id,ordinal,text,original_text,text_normalized,
                proposition_type,type_reason,claim_type,entities)
                VALUES ('prop_locator','sub_locator',1,'claim','claim','claim','factual','fixture','other',ARRAY['Philippine'])"""))
            conn.execute(text("""INSERT INTO research_runs (id,proposition_id,runner_version,model,config_json,status,started_at)
                VALUES ('run_locator','prop_locator','0.1.0','stub','{}','running',now())"""))
            conn.execute(text("""INSERT INTO documents (id,run_id,operator_added,url,fetch_status,text,paragraphs_json,fetched_at)
                VALUES ('doc_locator','run_locator',true,'https://example.test','ok',:body,
                        jsonb_build_array(jsonb_build_object('index',0,'start',0,'end',:length)),now())"""), dict(body=TEXT, length=len(TEXT)))
            insert = text("""INSERT INTO evidence (id,proposition_id,run_id,document_id,locator_type,locator_start,
                locator_end,paragraph_index,excerpt,source_class,relationship,strength,model_rationale)
                VALUES (:id,'prop_locator','run_locator','doc_locator','text_span',:start,:end,:paragraph,
                        :excerpt,'primary_record','supports','direct','fixture')""")
            valid = dict(id="ev_valid", start=0, end=len(TEXT), paragraph=0, excerpt=TEXT)
            conn.execute(insert, valid)
            for index, changes in enumerate([dict(excerpt="A paraphrased assertion that is not present."), dict(start=1), dict(paragraph=2), dict(excerpt=TEXT[:24], end=24)]):
                save = conn.begin_nested()
                with pytest.raises(IntegrityError) as error:
                    conn.execute(insert, {**valid, "id": f"ev_bad_{index}", **changes})
                assert error.value.orig.sqlstate == "23514"
                save.rollback()
        finally:
            tx.rollback()
    engine.dispose()
