"""Pure StatusLicenser. No I/O, clock, randomness, or model calls.

The explicit documents argument supplies manual ancestry referenced by the
specification's e.document notation; evidence stores only document_id.
"""
from veryjudge.anchors import covers, parse_anchor
from veryjudge.models import Document, Evidence, Proposition, StatusResult

CLOSURE_ELIGIBLE = set()
HIGH_RISK_EXEMPT = {"NOT_CHECKABLE", "NOT_ESTABLISHED"}


def is_primary(e):
    return e.source_class == "primary_record" or (
        e.source_class == "official_statement" and e.attests_own_act is True)


def is_original(e):
    return (e.source_class in {"wire", "direct_witness"}
            or (e.source_class == "official_statement" and e.attests_own_act is False)
            or (e.source_class == "original_reporting" and e.attribution in {"named_record", "named_person", "none"}))


def independent(rows, documents):
    return {documents[e.document_id].ancestry.root_document_id or e.document_id
            for e in rows if documents[e.document_id].ancestry.role != "carrier"}


def license(proposition: Proposition, evidence: list[Evidence], closure_eligible=None,
            *, documents: list[Document]) -> StatusResult:
    P = proposition
    if P.proposition_type != "factual":
        return StatusResult(status="NOT_CHECKABLE", rule_trace=["R0 type=" + P.proposition_type.value])
    if P.claim_type == "statistic" and parse_anchor(P.time_anchor) is None:
        return StatusResult(status="NOT_ESTABLISHED", review_reasons=["unanchored"], rule_trace=["R0a unanchored statistic"])
    closure_eligible = CLOSURE_ELIGIBLE if closure_eligible is None else closure_eligible
    docs = {d.id: d for d in documents}
    for e in evidence:
        d = docs.get(e.document_id)
        if d is None or d.run_id != e.run_id or e.proposition_id != P.id or d.fetch_status != "ok":
            # TODO: invalid/unbound packets have no licensing rule; abstain.
            return StatusResult(status="NOT_ESTABLISHED", rule_trace=["TODO ambiguous evidence packet"])
        root = docs.get(d.ancestry.root_document_id) if d.ancestry.root_document_id else d
        if root is None or root.run_id != d.run_id or (d.ancestry.root_document_id and
                (root.id == d.id or root.ancestry.role == "carrier")):
            # TODO: invalid manual ancestry has no licensing rule; abstain.
            return StatusResult(status="NOT_ESTABLISHED", rule_trace=["TODO ambiguous ancestry"])
    # The specification explicitly says any number of carriers is insufficient.
    D = [e for e in evidence if e.strength == "direct" and not e.removed_by_operator
         and docs[e.document_id].ancestry.role != "carrier"]
    S_prim = [e for e in D if e.relationship == "supports" and is_primary(e)]
    S_orig = [e for e in D if e.relationship == "supports" and is_original(e)]
    C_A = [e for e in D if e.relationship == "contradicts" and e.contradiction_route == "A" and is_primary(e)]
    C_B = [e for e in D if e.relationship == "contradicts" and e.contradiction_route == "B"
           and is_primary(e) and e.impossibility_note]
    C_C = [e for e in D if e.relationship == "contradicts" and e.contradiction_route == "C"
           and e.source_class == "primary_record" and (e.closure_record_key, P.claim_type) in closure_eligible
           and covers(e.closure_period, P.time_anchor)]
    support_ok = bool(S_prim) or len(independent(S_orig, docs)) >= 2
    contra_A_ok = len(independent(C_A, docs)) >= 1
    # User resolution: R4 must not depend on the absence of primary support.
    contra_C_ok = len(independent(C_C, docs)) >= 1
    contra_B_ok = bool(C_B)
    trace = [f"R1 primary_support={len(S_prim)} original_roots={len(independent(S_orig, docs))}",
             f"R2 route_A={contra_A_ok}", f"R3 route_B={contra_B_ok}", f"R4 route_C={contra_C_ok}"]
    if support_ok and (contra_A_ok or contra_C_ok):
        status, route, basis, rule = "CONFLICTING", None, S_prim + S_orig + C_A + C_C, "R5"
    elif contra_A_ok:
        status, route, basis, rule = "CONTRADICTED", "A", C_A, "R6"
    elif contra_C_ok:
        status, route, basis, rule = "CONTRADICTED", "C", C_C, "R6c"
    elif support_ok:
        status, route, basis, rule = "SUPPORTED", None, S_prim or S_orig, "R7"
    elif contra_B_ok:
        return StatusResult(status="PENDING_REVIEW", route="B", provisional_status="CONTRADICTED",
                            basis_evidence_ids=[e.id for e in C_B], review_reasons=["route_b_impossibility"],
                            rule_trace=trace + ["R8b"])
    else:
        status, route, basis, rule = "NOT_ESTABLISHED", None, [], "R8"
    trace.append(rule)
    if P.high_risk and status not in HIGH_RISK_EXEMPT:
        return StatusResult(status="PENDING_REVIEW", route=route, provisional_status=status,
                            basis_evidence_ids=[e.id for e in basis],
                            review_reasons=["high_risk:" + P.high_risk_category.value], rule_trace=trace + ["R9"])
    return StatusResult(status=status, route=route, basis_evidence_ids=[e.id for e in basis], rule_trace=trace)


class StatusLicenser:
    license = staticmethod(license)
