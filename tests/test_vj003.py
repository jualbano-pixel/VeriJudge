import ast
from pathlib import Path

import pytest

from veryjudge.licenser import CLOSURE_ELIGIBLE, license
from tests.helpers import candidate, document, evidence, proposition


def packet(rows, prop=None):
    p = prop or proposition()
    docs, evs = [], []
    for i, row in enumerate(rows):
        fields = dict(row)
        ancestry = fields.pop("ancestry", {"role": "originator", "root_document_id": None})
        d = document(id=f"doc_{i}", ancestry=ancestry)
        docs.append(d)
        evs.append(evidence(d, p, id=f"ev_{i}", **fields))
    return p, docs, evs


def result(rows=(), prop=None, closure_eligible=None):
    p, docs, evs = packet(rows, prop)
    return license(p, evs, closure_eligible, documents=docs)


def test_empty_evidence_not_established():
    assert result().status == "NOT_ESTABLISHED"


def test_failed_search_never_contradicted():
    assert result().status != "CONTRADICTED"


def test_affirmative_contradiction_primary():
    r = result([dict(relationship="contradicts", contradiction_route="A")])
    assert (r.status, r.route) == ("CONTRADICTED", "A")


def test_official_statement_own_act_licenses():
    assert result([dict(source_class="official_statement", attests_own_act=True)]).status == "SUPPORTED"


@pytest.mark.parametrize("relationship", ["supports", "contradicts"])
def test_official_statement_third_party_not_primary(relationship):
    assert result([dict(source_class="official_statement", attests_own_act=False,
                        relationship=relationship, contradiction_route="A")]).status == "NOT_ESTABLISHED"


def test_direct_witness_not_primary():
    row = dict(source_class="direct_witness")
    assert result([row]).status == "NOT_ESTABLISHED"
    assert result([row, row]).status == "SUPPORTED"


def test_unnamed_attribution_never_licenses():
    assert result([dict(source_class="original_reporting", attribution="unnamed")] * 2).status == "NOT_ESTABLISHED"


def test_two_independent_originals_support():
    row = dict(source_class="original_reporting", attribution="named_person")
    assert result([row, row]).status == "SUPPORTED"
    assert result([row, {**row, "ancestry": dict(role="originator", root_document_id="doc_0")}]).status == "NOT_ESTABLISHED"


def test_carriers_do_not_stack():
    original = dict(source_class="wire")
    carrier = dict(source_class="wire", ancestry=dict(role="carrier", root_document_id="doc_0"))
    assert result([original, carrier, carrier, carrier]).status == "NOT_ESTABLISHED"
    # Even a primary-class carrier cannot license alone under the explicit rule notes.
    assert result([dict(source_class="reference"), {**carrier, "source_class": "primary_record"}]).status == "NOT_ESTABLISHED"


@pytest.mark.parametrize("source", ["original_reporting", "wire", "republication", "fact_check", "reference", "user_generated", "direct_witness"])
def test_secondary_contradiction_insufficient(source):
    assert result([dict(source_class=source, attribution="named_record", relationship="contradicts", contradiction_route="A")]).status == "NOT_ESTABLISHED"


@pytest.mark.parametrize("strength", ["indirect", "weak"])
@pytest.mark.parametrize("relationship", ["supports", "contradicts"])
def test_indirect_never_licenses(strength, relationship):
    assert result([dict(strength=strength, relationship=relationship, contradiction_route="A")]).status == "NOT_ESTABLISHED"


def test_model_unlikely_is_not_evidence():
    assert result([dict(relationship="contextualizes", model_rationale="unlikely, probably false")]).status == "NOT_ESTABLISHED"


@pytest.mark.parametrize("anchor", [None, "next month"])
def test_unanchored_statistic_not_established(anchor):
    r = result([{}], proposition(time_anchor=anchor))
    assert r.status == "NOT_ESTABLISHED" and "unanchored" in r.review_reasons


def test_route_b_pending_review():
    row = dict(relationship="contradicts", contradiction_route="B", impossibility_note="Record makes the dates impossible")
    r = result([row])
    assert (r.status, r.provisional_status, r.route) == ("PENDING_REVIEW", "CONTRADICTED", "B")
    assert "route_b_impossibility" in r.review_reasons
    assert result([{}, row]).status == "SUPPORTED"


def test_closure_list_empty_in_production():
    assert CLOSURE_ELIGIBLE == set()


def closure_row(**changes):
    return {**dict(relationship="contradicts", contradiction_route="C", closure_record_key="TEST_ONLY_REGISTER", closure_period="2020"), **changes}


def test_route_c_with_test_register():
    injected = {("TEST_ONLY_REGISTER", "statistic")}
    row = closure_row()
    r = result([row], closure_eligible=injected)
    assert (r.status, r.route) == ("CONTRADICTED", "C")
    assert result([row]).status == "NOT_ESTABLISHED"
    assert result([{}, row], closure_eligible=injected).status == "CONFLICTING"
    assert result([closure_row(closure_record_key="wrong")], closure_eligible=injected).status == "NOT_ESTABLISHED"
    assert result([closure_row(closure_period="2020-01")], closure_eligible=injected).status == "NOT_ESTABLISHED"
    assert result([row], proposition(claim_type="event"), closure_eligible=injected).status == "NOT_ESTABLISHED"
    assert result([closure_row(source_class="official_statement", attests_own_act=True)], closure_eligible=injected).status == "NOT_ESTABLISHED"
    assert CLOSURE_ELIGIBLE == set()


def test_conflicting_direct_both_sides():
    assert result([{}, dict(relationship="contradicts", contradiction_route="A")]).status == "CONFLICTING"


@pytest.mark.parametrize("rows,provisional", [
    ([{}], "SUPPORTED"),
    ([dict(relationship="contradicts", contradiction_route="A")], "CONTRADICTED"),
    ([{}, dict(relationship="contradicts", contradiction_route="A")], "CONFLICTING"),
])
def test_high_risk_gate(rows, provisional):
    r = result(rows, proposition(high_risk_category="health"))
    assert r.status == "PENDING_REVIEW" and r.provisional_status == provisional
    assert r.review_reasons == ["high_risk:health"]


def test_high_risk_exempt():
    assert result([], proposition(high_risk_category="health")).status == "NOT_ESTABLISHED"
    assert result([{}], proposition(high_risk_category="health", proposition_type="opinion")).status == "NOT_CHECKABLE"


def test_licenser_is_deterministic():
    p, docs, evs = packet([{}])
    before = [e.model_dump() for e in evs]
    expected = license(p, evs, documents=docs)
    assert all(license(p, evs, documents=docs) == expected for _ in range(100))
    assert before == [e.model_dump() for e in evs]
    tree = ast.parse(Path("veryjudge/licenser.py").read_text())
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imports == {"veryjudge.anchors", "veryjudge.models"}
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id in {"open", "exec", "eval", "print", "input"} for node in ast.walk(tree))
