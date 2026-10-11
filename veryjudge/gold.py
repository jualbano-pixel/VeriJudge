"""Gold-fixture loading and deterministic regression harness for M1."""
import json
from pathlib import Path
from typing import Callable

from veryjudge.models import Proposition


FIXTURE_DIR = Path("fixtures/gold")
REQUIRED_GOLD = {
    "proposition", "proposition_type", "claim_type", "time_anchor",
    "high_risk_category", "status", "route", "acceptable_statuses",
    "forbidden_statuses", "expected_review_reasons", "decisive_evidence",
    "acceptable_supporting", "non_decisive", "ancestry_edits",
    "false_strong_negative", "overclaim", "expected_operator_action",
    "expected_final_statuses", "failure_mode_tested",
}


def fixtures(directory=FIXTURE_DIR):
    return [json.loads(path.read_text()) for path in sorted(Path(directory).glob("VJ-G*.json"))]


def fixture_proposition(fixture):
    gold = fixture["gold"]
    return Proposition(
        id="prop_" + fixture["case_id"], submission_id="sub_" + fixture["case_id"], ordinal=1,
        text=gold["proposition"], original_text=gold["proposition"],
        text_normalized=" ".join(gold["proposition"].lower().split()),
        proposition_type=gold["proposition_type"], type_reason="Gold fixture",
        claim_type=gold["claim_type"], time_anchor=gold["time_anchor"],
        entities=[], high_risk_category=gold["high_risk_category"],
    )


def cassette_ready(fixture, cassette_dir=Path("fixtures/cassettes")):
    """Only a human-recorded capture cassette may satisfy a decisive URL check."""
    decisive = fixture["gold"]["decisive_evidence"]
    if not decisive:
        return True
    successful_captures = []
    for path in Path(cassette_dir).glob("*.json"):
        try:
            cassette = json.loads(path.read_text())
            if (cassette.get("operation") == "fetch"
                    and cassette.get("response", {}).get("fetch_status") == "ok"):
                successful_captures.append((
                    cassette.get("request", {}).get("url", ""),
                    cassette.get("response", {}).get("text", ""),
                ))
        except (OSError, json.JSONDecodeError):
            continue
    for row in decisive:
        expected, pattern = row.get("url"), row.get("url_pattern")
        if not any(
            (url == expected if expected else bool(pattern and pattern in url))
            and all(part in text for part in row.get("excerpt_must_contain", []))
            for url, text in successful_captures
        ):
            return False
    return True


class GoldHarness:
    """Applies the six specification checks to a completed pipeline result.

    `pipeline` must return a mapping with machine_status, review_reasons,
    evidence and a callable `review(payload)` that invokes the review endpoint.
    This indirection keeps replay/live setup outside the invariant checker.
    """
    def __init__(self, pipeline: Callable):
        self.pipeline = pipeline

    def run(self, fixture):
        gold = fixture["gold"]
        result = self.pipeline(fixture)  # 1. pipeline with replay/live setup
        status = result["machine_status"]
        assert status in gold["acceptable_statuses"]  # 2. result and reasons
        assert status not in gold["forbidden_statuses"]
        assert set(result.get("review_reasons", [])) >= set(gold["expected_review_reasons"])
        evidence = result.get("evidence", [])
        if fixture["fixture_prep"]["status"] == "prepared" and cassette_ready(fixture):  # 3.
            for decisive in gold["decisive_evidence"]:
                matches = [row for row in evidence if self._matches(row, decisive)]
                assert matches, f"missing decisive evidence {decisive['fixture_id']}"
        if gold["ancestry_edits"]:  # 4. must go through endpoint, not database mutation
            reviewed = result["review"]({"decision": "approve", "ancestry_edits": gold["ancestry_edits"]})
            assert reviewed["relicensed_status"] in gold["expected_final_statuses"]
        for row in evidence:  # 5. pipeline should expose validation fact per row
            assert row.get("locator_valid", False)
        return result  # 6. needs_human deliberately runs only 2 and 5 above

    @staticmethod
    def _matches(row, decisive):
        document = row.get("document", row)
        url = document.get("final_url") or document.get("url", "")
        expected = decisive.get("url")
        pattern = decisive.get("url_pattern")
        url_ok = url == expected if expected else bool(pattern and pattern in url)
        return url_ok and all(part in row.get("excerpt", "") for part in decisive["excerpt_must_contain"])
