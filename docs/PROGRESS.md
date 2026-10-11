# Ticket acceptance ledger

## Environment

Docker Desktop was installed by the user after Homebrew's privileged install
step was blocked. Docker Engine 29.8.0 is running. The unchanged Compose file
starts `postgres:15`, verified as PostgreSQL 15.19, healthy on localhost:5432.

## VJ-001 — passed

Real `alembic upgrade head` succeeded. All five acceptance tests passed:
health/auth, StubLLM, ULIDs, config limits, and Postgres schema constraints.

## VJ-002 — passed

Typed objects/enums, strict anchor parsing, paragraph-bound locators, required
class fields, and explanation mapping. Cross-table database invariants use a
constraint trigger raising 23514 without adding tables or columns.
All tests through VJ-002: 47 passed.

## VJ-003 — passed

Pure deterministic StatusLicenser; production closure eligibility stays empty.
The user's route-C correction is documented in SPEC_QUESTIONS.md.
All tests through VJ-003: **80 passed**, including real Postgres tests.

## VJ-004 — passed

Web and curated Philippine search providers with JSON record/replay, CI
live-call refusal, provider error replay, and complete retrieval-step data.
Six acceptance tests passed. No live search was performed.

## VJ-005 — passed

Per-hop destination validation and IP-pinned HTTP connections, HTML/PDF
extraction, normalization, paragraph offsets, raw local capture and fetch
record/replay. Eighteen tests passed with mocked HTTP/DNS and generated PDF
data. No live source capture was performed. Failed fetch database insertion
is integrated with the runner in VJ-008, not within the fetch adapter.

## VJ-006 — passed

POST /submissions, StubLLM claim extraction, field persistence, anchor
normalization and stored extraction failure path. Seven tests passed.

## VJ-007 — passed

Select/edit, atomic selection locking, NOT_CHECKABLE shortcut and in-process
thread-pool dispatch. Seven tests passed. Tests inject a stub handler; the
default application handler is not yet wired to the VJ-008 runner.

## VJ-008 — passed

SingleAgentRunner, two-worker executor, per-run watchdog, startup orphan
reaper, pre-execution step persistence, single result finalization, capture
provenance, deduplication, budgets and StubEvidenceExtractor are implemented.
The user resolved lifecycle immutability as freeze after finalization; the
resolution is recorded in SPEC_QUESTIONS.md.

## VJ-009 — passed

Evidence extraction gets one exact-quote retry. Rejected candidates are only
counted and logged locally; valid candidates pass class and locator checks.

## VJ-010 — passed

ExplanationWriter validates evidence mapping, retries once, then uses the
constrained template with trace limitations.

## VJ-011 — passed

Run-scoped licensing and judgment assembly, polling, retry supersession and
operator/viewer exposure rules are implemented.

## VJ-012 — passed

Transactional review applies ancestry, added evidence, removals, re-licensing,
transition validation and immutable audit insertion in the required order.
VJ-012's review and transition suite: 314 passed.

## VJ-013 — passed

Jinja local operator UI implements Screens 1–4, minimal polling, evidence
chips, trace toggle and review panel. It is rendered with `python -m
veryjudge.ui`; this preserves the specified six JSON application endpoints
(plus the VJ-001 health acceptance endpoint) rather than adding a UI route.

## VJ-014 — passed

Fifteen fixture files and the gold harness are present. The harness checks
fixture shape, deterministic licensing, valid locator reporting and routes
ancestry edits through the review callback. Decisive-capture assertions run
only when their matching VJ-015 fetch cassettes exist.

## Current verification

Full implemented suite: **502 passed, 1 skipped, 1 warning** on Docker
Postgres 15. The warning concerns urllib3 on the system Python's LibreSSL
build; no live provider tests were run. The remaining skip is VJ-G12, an
intentionally unresolved conflict benchmark; it is not awaiting retrieval.
The blind-label end-to-end replay audit passes. Command:

```sh
TEST_DATABASE_URL='postgresql+psycopg://veryjudge:veryjudge@127.0.0.1:5432/veryjudge' \
  .venv/bin/python -m pytest -q
```

## VJ-015 — complete

Human-approved evidence packets and cassettes were recorded for the completed
gold benchmarks. G12 remains skipped by design: extensive Iran/OFW and wider
Philippine emergency-response searches found no pair of first-party official
sources that reported materially different, like-for-like facts for the same
cutoff. Every apparent discrepancy was explained by reporting time, geography,
metric, or operational definition. The benchmark is intentionally unresolved;
the skip does not request a human click, download, or source-selection action.
No conflict criteria were weakened and no synthetic conflict was created.
