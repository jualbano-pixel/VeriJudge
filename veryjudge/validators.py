"""Pure validators; stored text is never normalized here."""
from typing import Optional

from veryjudge.anchors import parse_anchor
from veryjudge.models import Document, EvidenceCandidate, Evidence, Explanation, Model, Proposition


def class_field_error(candidate) -> Optional[str]:
    if candidate.source_class == "official_statement" and candidate.attests_own_act is None:
        return "attests_own_act required"
    if candidate.source_class == "original_reporting" and candidate.attribution is None:
        return "attribution required"
    if candidate.relationship == "contradicts" and not candidate.contradiction_route:
        return "contradiction_route required"
    if candidate.contradiction_route == "B" and not (candidate.impossibility_note or "").strip():
        return "impossibility_note required"
    if candidate.contradiction_route == "C":
        if not (candidate.closure_record_key or "").strip() or not parse_anchor(candidate.closure_period):
            return "closure_record_key and parseable closure_period required"
    return None


class LocatorResult(Model):
    ok: bool
    locator_start: Optional[int] = None
    locator_end: Optional[int] = None
    reason: Optional[str] = None


class LocatorValidator:
    @staticmethod
    def validate(document: Document, candidate: EvidenceCandidate, proposition: Proposition) -> LocatorResult:
        def reject(reason):
            return LocatorResult(ok=False, reason=reason)

        if document.fetch_status != "ok" or document.text is None:
            return reject("failed_document")
        reason = class_field_error(candidate)
        if reason:
            return reject(reason)
        if not 25 <= len(candidate.excerpt) <= 300:
            return reject("excerpt_length")
        paragraph = next((p for p in document.paragraphs if p.index == candidate.paragraph_index), None)
        if paragraph is None:
            return reject("paragraph_missing")
        paragraph_text = document.text[paragraph.start:paragraph.end]
        occurrence = paragraph_text.find(candidate.excerpt)
        if occurrence < 0:
            return reject("not_found")
        if not any(entity.strip() and entity.casefold() in paragraph_text.casefold() for entity in proposition.entities):
            return reject("entity_overlap")
        start = paragraph.start + occurrence
        return LocatorResult(ok=True, locator_start=start, locator_end=start + len(candidate.excerpt))


def validate_evidence(evidence: Evidence, document: Document, proposition: Proposition, run_proposition_id: str):
    if evidence.document_id != document.id or evidence.run_id != document.run_id:
        raise ValueError("evidence/document run mismatch")
    if evidence.proposition_id != proposition.id or evidence.proposition_id != run_proposition_id:
        raise ValueError("evidence/proposition run mismatch")
    fields = {k: getattr(evidence, k) for k in EvidenceCandidate.model_fields if k not in {"paragraph_index", "rationale"}}
    candidate = EvidenceCandidate(**fields, paragraph_index=evidence.locator.paragraph_index, rationale=evidence.model_rationale)
    result = LocatorValidator.validate(document, candidate, proposition)
    if not result.ok:
        raise ValueError(result.reason)
    if (evidence.locator.start, evidence.locator.end) != (result.locator_start, result.locator_end):
        raise ValueError("locator does not match first occurrence in stated paragraph")


def validate_explanation(value, evidence: list[Evidence]) -> Explanation:
    explanation = value if isinstance(value, Explanation) else Explanation.model_validate(value)
    valid_ids = {e.id for e in evidence if not e.removed_by_operator}
    for sentence in explanation.sentences:
        if any(eid not in valid_ids for eid in sentence.evidence_ids):
            raise ValueError("explanation references missing or removed evidence")
    return explanation


def validate_ancestry(documents: list[Document]):
    by_id = {d.id: d for d in documents}
    for document in documents:
        root_id = document.ancestry.root_document_id
        if root_id:
            root = by_id.get(root_id)
            if not root or root.id == document.id or root.run_id != document.run_id or root.ancestry.role == "carrier":
                raise ValueError("root must be same-run, non-carrier, non-self")
