"""VJ-013 static screen contract and stubbed operator walkthrough fixtures."""
from pathlib import Path

from veryjudge.ui import render


def test_operator_page_has_all_four_screens_and_review(tmp_path):
    page = render(tmp_path / "operator.html", "http://127.0.0.1:8000")
    content = page.read_text()
    for expected in (
        "VeryJudge — check a claim",
        "Paste the claim exactly as you saw it.",
        "Which statement should we check?",
        "This is one checkable statement with one subject and one predicate.",
        "Research in progress",
        "Searches 0 / 8",
        "Claim as understood",
        "Show retrieval trace",
        "Review panel",
        "operator added",
        "Raw retrieval trace",
    ):
        assert expected in content
    assert "<script>const API=\"http://127.0.0.1:8000\";" in content


def test_operator_javascript_is_syntax_valid():
    # Kept as a source-contract test so packaging and a browser parse the same file.
    source = Path("veryjudge/static/operator.js").read_text()
    assert "startPolling" in source
    assert "Save review" in Path("veryjudge/templates/operator.html").read_text()
    assert "api('/runs/'+id)" in source
