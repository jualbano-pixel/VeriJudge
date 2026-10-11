from datetime import datetime,timezone

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import IntegrityError

from veryjudge.app import create_app
from veryjudge.db import object_from_row,row_from_object
from veryjudge.ids import new_id
from veryjudge.judgments import JudgmentService
from veryjudge.llm import StubLLM
from veryjudge.models import Proposition
from veryjudge.review import transition
from tests.helpers import TEXT,document,evidence
from tests.test_vj006 import AUTH,repository,settings
from tests.test_vj008_lifecycle import create_run,read_run
from tests.test_vj008_loop import FetchStub
from tests.test_vj011 import terminal,VIEWER


def seed(db,supported=False,high_risk=False):
    rid=create_run(db)
    pid=read_run(db,rid)["proposition_id"]
    eid,did=None,None
    with db.transaction() as conn:
        if high_risk:
            table=db.tables["propositions"]
            conn.execute(table.update().where(table.c.id==pid).values(high_risk_category="health"))
        if supported:
            prop=object_from_row(Proposition,db.get(conn,"propositions",pid))
            did=new_id("doc")
            doc=document(id=did,run_id=rid,retrieval_step_id=None,operator_added=True,url="https://example.test/initial")
            row=evidence(doc,prop,id=new_id("ev"))
            eid=row.id
            db.insert(conn,"documents",row_from_object(doc,db.tables["documents"]))
            db.insert(conn,"evidence",row_from_object(row,db.tables["evidence"]))
    terminal(db,rid,"done")
    jid=JudgmentService(db,StubLLM()).complete(rid)
    return jid,eid,did


def added(**changes):
    return {**dict(url="https://example.test/added",paragraph_index=0,excerpt=TEXT,source_class="primary_record",
                   relationship="supports",strength="direct"),**changes}


def client(db):
    return TestClient(create_app(settings(),database=db,review_fetcher=FetchStub()))


def test_downgrade_preserved(repository):
    jid,_,_=seed(repository,True)
    with client(repository) as c:
        response=c.post(f"/judgments/{jid}/review",headers=AUTH,json=dict(decision="downgrade",final_status="NOT_ESTABLISHED",error_class="overclaim",machine_error_reason="Insufficient scope"))
        assert response.status_code==200,response.text
        j=response.json()["judgment"]
        assert j["machine_status"]=="SUPPORTED" and j["final_status"]=="NOT_ESTABLISHED"
        assert j["reviews"][0]["status_before"]=="SUPPORTED"


def test_naked_upgrade_blocked(repository):
    jid,_,_=seed(repository)
    with client(repository) as c:
        assert c.post(f"/judgments/{jid}/review",headers=AUTH,json=dict(decision="approve",final_status="SUPPORTED")).status_code==400


def test_operator_added_evidence_validated(repository):
    jid,_,_=seed(repository)
    with client(repository) as c:
        response=c.post(f"/judgments/{jid}/review",headers=AUTH,json=dict(decision="approve",final_status="SUPPORTED",added_evidence=[added()]))
        assert response.status_code==200,response.text
        j=response.json()["judgment"]
        assert j["machine_status"]=="NOT_ESTABLISHED" and j["relicensed_status"]=="SUPPORTED" and j["final_status"]=="SUPPORTED"
        assert j["evidence"][0]["operator_added"] and j["documents"][0]["operator_added"]
        assert j["reviews"][0]["added_evidence_ids"]==[j["evidence"][0]["id"]]


def test_operator_added_evidence_invalid_rejected(repository):
    jid,_,_=seed(repository)
    with client(repository) as c:
        response=c.post(f"/judgments/{jid}/review",headers=AUTH,json=dict(decision="approve",added_evidence=[added(paragraph_index=9)]))
        assert response.status_code==400
        j=c.get(f"/judgments/{jid}",headers=AUTH).json()
        assert not j["evidence"] and not j["documents"] and not j["reviews"]


@pytest.mark.parametrize("url",["http://169.254.169.254/latest","http://10.0.0.1/private"])
def test_operator_added_url_private_ip_blocked(repository,url):
    jid,_,_=seed(repository)
    with client(repository) as c:
        assert c.post(f"/judgments/{jid}/review",headers=AUTH,json=dict(decision="approve",added_evidence=[added(url=url)])).status_code==400


def test_non_approve_requires_reason(repository):
    jid,_,_=seed(repository,True)
    with client(repository) as c:
        assert c.post(f"/judgments/{jid}/review",headers=AUTH,json=dict(decision="reject")).status_code==400


def test_remove_evidence_relicenses(repository):
    jid,eid,_=seed(repository,True)
    with client(repository) as c:
        response=c.post(f"/judgments/{jid}/review",headers=AUTH,json=dict(decision="approve",final_status="NOT_ESTABLISHED",removed_evidence_ids=[eid]))
        assert response.status_code==200,response.text
        j=response.json()["judgment"]
        assert j["relicensed_status"]=="NOT_ESTABLISHED" and j["evidence"][0]["removed_by_operator"]
        assert j["machine_status"]=="SUPPORTED"


def test_pending_review_requires_final_status(repository):
    jid,_,_=seed(repository,True,True)
    with client(repository) as c:
        assert c.post(f"/judgments/{jid}/review",headers=AUTH,json=dict(decision="approve")).status_code==400
        assert c.post(f"/judgments/{jid}/review",headers=AUTH,json=dict(decision="approve",final_status="SUPPORTED")).status_code==200


WEAKER={"SUPPORTED":{"CONFLICTING","NOT_ESTABLISHED","NOT_CHECKABLE"},
        "CONTRADICTED":{"CONFLICTING","NOT_ESTABLISHED","NOT_CHECKABLE"},
        "CONFLICTING":{"NOT_ESTABLISHED","NOT_CHECKABLE"},"NOT_ESTABLISHED":{"NOT_CHECKABLE"},"NOT_CHECKABLE":set()}


@pytest.mark.parametrize("base",list(WEAKER))
@pytest.mark.parametrize("pending",[False,True])
@pytest.mark.parametrize("relicensed",list(WEAKER)+["PENDING_REVIEW"])
@pytest.mark.parametrize("final",list(WEAKER))
def test_transition_table(base,pending,relicensed,final):
    args=("PENDING_REVIEW" if pending else base,base if pending else None,relicensed,final,"approve",True)
    if final in WEAKER[base]|{base,relicensed}:
        assert transition(*args)==final
    else:
        with pytest.raises(ValueError):
            transition(*args)


def test_ancestry_same_run_non_carrier_root(repository):
    jid,_,did=seed(repository,True)
    _,_,other=seed(repository,True)
    with client(repository) as c:
        assert c.post(f"/judgments/{jid}/review",headers=AUTH,json=dict(decision="approve",ancestry_edits=[dict(document_id=did,ancestry_role="carrier",root_document_id=other)])).status_code==400


def test_ancestry_self_reference_rejected(repository):
    jid,_,did=seed(repository,True)
    with client(repository) as c:
        assert c.post(f"/judgments/{jid}/review",headers=AUTH,json=dict(decision="approve",ancestry_edits=[dict(document_id=did,ancestry_role="carrier",root_document_id=did)])).status_code==400


def test_review_superseded_409(repository):
    jid,_,_=seed(repository)
    replacement,_,_=seed(repository)
    with repository.transaction() as conn:
        table=repository.tables["judgments"]
        conn.execute(table.update().where(table.c.id==jid).values(superseded_by=replacement))
    with client(repository) as c:
        assert c.post(f"/judgments/{jid}/review",headers=AUTH,json=dict(decision="approve")).status_code==409


def test_viewer_cannot_review(repository):
    jid,_,_=seed(repository)
    with client(repository) as c:
        assert c.post(f"/judgments/{jid}/review",headers=VIEWER,json=dict(decision="approve")).status_code==404


def test_machine_originals_immutable(repository):
    jid,_,_=seed(repository)
    with pytest.raises(IntegrityError):
        with repository.transaction() as conn:
            table=repository.tables["judgments"]
            conn.execute(table.update().where(table.c.id==jid).values(machine_status="SUPPORTED"))
