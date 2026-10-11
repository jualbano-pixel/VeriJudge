"""Atomic operator review. Machine originals are never edited."""
from datetime import datetime,timezone

from veryjudge.db import row_from_object
from veryjudge.fetcher import check_url
from veryjudge.ids import new_id
from veryjudge.licenser import CLOSURE_ELIGIBLE,license
from veryjudge.models import Ancestry,Document,Evidence,EvidenceCandidate,Explanation,Locator,Sentence
from veryjudge.validators import LocatorValidator,validate_ancestry,validate_explanation

RANK={"SUPPORTED":3,"CONTRADICTED":3,"CONFLICTING":2,"NOT_ESTABLISHED":1,"NOT_CHECKABLE":0}


def transition(machine,provisional,relicensed,requested,decision,has_added):
    base=provisional if machine=="PENDING_REVIEW" else machine
    if machine=="PENDING_REVIEW" and requested is None:
        raise ValueError("PENDING_REVIEW requires final_status")
    final=requested or base
    if decision=="reject":
        final=final if final in RANK and RANK[final]<RANK["NOT_ESTABLISHED"] else "NOT_ESTABLISHED"
    if base not in RANK or final not in RANK:
        raise ValueError("invalid final status")
    allowed=final==base or final==relicensed or RANK[final]<RANK[base]
    if not allowed:
        raise ValueError("disallowed status transition")
    if RANK[final]>RANK[base] and not has_added:
        raise ValueError("upgrades require operator-added evidence")
    if decision=="downgrade" and RANK[final]>=RANK[base]:
        raise ValueError("downgrade requires a weaker final_status")
    return final


class ReviewService:
    def __init__(self,repository,judgments,fetcher):
        self.repository,self.judgments,self.fetcher=repository,judgments,fetcher

    def apply(self,conn,judgment,request):
        db=self.repository
        if request.decision!="approve" and (not request.error_class or not (request.machine_error_reason or "").strip()):
            raise ValueError("error_class and machine_error_reason required")
        if judgment["machine_status"]=="PENDING_REVIEW" and request.final_status is None:
            raise ValueError("PENDING_REVIEW requires final_status")
        prop,evidence,documents,steps=self.judgments.packet(conn,judgment,include_removed=True)
        by_doc={d.id:d for d in documents}
        by_ev={e.id:e for e in evidence}
        for edit in request.ancestry_edits:
            if edit.document_id not in by_doc:
                raise ValueError("ancestry document not in judgment run")
            by_doc[edit.document_id]=by_doc[edit.document_id].model_copy(update={"ancestry":Ancestry(role=edit.ancestry_role,root_document_id=edit.root_document_id)})
        validate_ancestry(list(by_doc.values()))
        doc_table=db.tables["documents"]
        for edit in request.ancestry_edits:
            conn.execute(doc_table.update().where(doc_table.c.id==edit.document_id).values(ancestry_role=edit.ancestry_role,root_document_id=edit.root_document_id))
        added=[]
        attempts=len(documents)
        captures=sum(d.fetch_status=="ok" for d in documents)
        for item in request.added_evidence:
            if not judgment["run_id"]:
                raise ValueError("cannot add evidence to a no-run judgment")
            if attempts>=15 or captures>=10:
                raise ValueError("run fetch budget exhausted")
            if any(item.url in {d.url,d.final_url} for d in by_doc.values()):
                raise ValueError("URL already captured in this run")
            check_url(item.url)
            did=new_id("doc")
            fetched=self.fetcher.fetch(item.url,20,run_id=judgment["run_id"],document_id=did)
            attempts+=1
            if fetched.fetch_status!="ok":
                raise ValueError("added evidence fetch failed: "+str(fetched.failure_reason))
            if any(fetched.final_url and fetched.final_url in {d.url,d.final_url} for d in by_doc.values()):
                raise ValueError("URL already captured in this run")
            doc=Document(**fetched.model_dump(),id=did,run_id=judgment["run_id"],operator_added=True)
            candidate=EvidenceCandidate(**item.model_dump(exclude={"url"}),rationale="operator")
            locator=LocatorValidator.validate(doc,candidate,prop)
            if not locator.ok:
                raise ValueError("invalid added evidence: "+str(locator.reason))
            row=Evidence(**candidate.model_dump(exclude={"paragraph_index","rationale"}),id=new_id("ev"),
                         proposition_id=prop.id,run_id=judgment["run_id"],document_id=did,operator_added=True,
                         locator=Locator(start=locator.locator_start,end=locator.locator_end,paragraph_index=candidate.paragraph_index),model_rationale="operator")
            db.insert(conn,"documents",row_from_object(doc,doc_table))
            db.insert(conn,"evidence",row_from_object(row,db.tables["evidence"]))
            by_doc[doc.id]=doc
            by_ev[row.id]=row
            added.append(row.id)
            captures+=1
        for eid in request.removed_evidence_ids+request.flagged_evidence_ids:
            if eid not in by_ev:
                raise ValueError("evidence not in judgment run")
        ev_table=db.tables["evidence"]
        for eid in request.removed_evidence_ids:
            conn.execute(ev_table.update().where(ev_table.c.id==eid).values(removed_by_operator=True))
            by_ev[eid]=by_ev[eid].model_copy(update={"removed_by_operator":True})
        active=[e for e in by_ev.values() if not e.removed_by_operator]
        relicensed=license(prop,active,CLOSURE_ELIGIBLE,documents=list(by_doc.values()))
        final=transition(judgment["machine_status"],judgment["provisional_status"],relicensed.status,
                         request.final_status,request.decision,bool(added))
        if request.final_explanation is not None:
            explanation=validate_explanation(request.final_explanation,active)
        else:
            try:
                explanation=validate_explanation(judgment["machine_explanation_json"],active)
                if final!=judgment["machine_status"]:
                    raise ValueError("status changed")
            except ValueError:
                explanation=Explanation(sentences=[Sentence(text=final+". See the evidence table.",kind="meta",evidence_ids=[])],what_would_change=[],limitations=[])
        for eid in request.flagged_evidence_ids:
            conn.execute(ev_table.update().where(ev_table.c.id==eid).values(extraction_flag=True))
        if request.proposition_flag:
            table=db.tables["propositions"]
            conn.execute(table.update().where(table.c.id==prop.id).values(extraction_flag=True))
        review_id=new_id("rev")
        db.insert(conn,"reviews",dict(id=review_id,judgment_id=judgment["id"],decision=request.decision,
            status_before=judgment["final_status"] or judgment["machine_status"],relicensed_status=relicensed.status.value,status_after=final,
            removed_evidence_ids=request.removed_evidence_ids,flagged_evidence_ids=request.flagged_evidence_ids,
            added_evidence_ids=added,ancestry_edits_json=[e.model_dump() for e in request.ancestry_edits],
            explanation_edited=request.final_explanation is not None,proposition_flagged=request.proposition_flag,
            error_class=request.error_class,machine_error_reason=request.machine_error_reason))
        table=db.tables["judgments"]
        conn.execute(table.update().where(table.c.id==judgment["id"]).values(relicensed_status=relicensed.status.value,
            relicensed_route=relicensed.route,review_state="rejected" if request.decision=="reject" else "approved",
            final_status=final,final_explanation_json=explanation.model_dump(mode="json")))
        return review_id
