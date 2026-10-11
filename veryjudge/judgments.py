"""Run-scoped licensing, judgment assembly and retrieval."""
from datetime import datetime, timezone

from veryjudge.db import object_from_row
from veryjudge.explanation import ExplanationWriter
from veryjudge.ids import new_id
from veryjudge.licenser import CLOSURE_ELIGIBLE, license
from veryjudge.models import Document, Evidence, Proposition


class JudgmentService:
    def __init__(self, repository, llm):
        self.repository=repository
        self.writer=ExplanationWriter(llm)

    def packet(self,conn,judgment,include_removed=False):
        db=self.repository
        prop=object_from_row(Proposition,db.get(conn,"propositions",judgment["proposition_id"]))
        if not judgment["run_id"]:
            return prop,[],[],[]
        docs=[object_from_row(Document,r) for r in db.rows(conn,"documents",run_id=judgment["run_id"])]
        evidence=[object_from_row(Evidence,r) for r in db.rows(conn,"evidence",run_id=judgment["run_id"])
                  if include_removed or not r["removed_by_operator"]]
        steps=sorted(db.rows(conn,"retrieval_steps",run_id=judgment["run_id"]),key=lambda r:r["step_number"])
        return prop,evidence,docs,steps

    def complete(self,run_id):
        db=self.repository
        with db.transaction() as conn:
            run=db.get(conn,"research_runs",run_id)
            if not run or run["status"] not in {"done","timeout"}:
                return
            prop,evidence,docs,steps=self.packet(conn,{**run,"run_id":run_id})
        licensed=license(prop,evidence,CLOSURE_ELIGIBLE,documents=docs)
        explanation=self.writer.write(prop,licensed,evidence,docs,steps)
        with db.transaction() as conn:
            db.get(conn,"research_runs",run_id,for_update=True)
            existing=db.rows(conn,"judgments",run_id=run_id)
            if existing:
                return existing[0]["id"]
            jid=new_id("jdg")
            db.insert(conn,"judgments",dict(id=jid,proposition_id=prop.id,run_id=run_id,
                machine_status=licensed.status.value,machine_route=licensed.route,provisional_status=licensed.provisional_status,
                basis_evidence_ids=licensed.basis_evidence_ids,rule_trace=licensed.rule_trace,review_reasons=licensed.review_reasons,
                machine_explanation_json=explanation.model_dump(mode="json"),as_of=datetime.now(timezone.utc)))
            table=db.tables["judgments"]
            for prior in db.rows(conn,"judgments",proposition_id=prop.id):
                if prior["run_id"] and prior["id"]!=jid and not prior["superseded_by"]:
                    prior_run=db.get(conn,"research_runs",prior["run_id"])
                    if prior_run["started_at"] < run["started_at"]:
                        conn.execute(table.update().where(table.c.id==prior["id"]).values(superseded_by=jid))
            return jid

    def full(self,conn,judgment,viewer=False):
        db=self.repository
        prop,evidence,docs,steps=self.packet(conn,judgment,include_removed=True)
        result=dict(judgment)
        result["machine_explanation"]=result.pop("machine_explanation_json")
        result["final_explanation"]=result.pop("final_explanation_json")
        result["evidence"]=[e.model_dump(mode="json") for e in evidence]
        result["documents"]=[d.model_dump(mode="json") for d in docs]
        result["proposition"]=prop.model_dump(mode="json")
        result["submitted_text"]=db.get(conn,"submissions",prop.submission_id)["text"]
        run=db.get(conn,"research_runs",judgment["run_id"]) if judgment["run_id"] else None
        result["runner_version"]=run["runner_version"] if run else None
        if viewer:
            result.pop("rule_trace",None)
        else:
            result["reviews"]=db.rows(conn,"reviews",judgment_id=judgment["id"])
            result["trace"]=dict(steps=steps,stop_reason=run["stop_reason"] if run else None,
                                 rejected_evidence_count=run["rejected_evidence_count"] if run else 0)
        return result
