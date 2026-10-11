# VeryJudge — Milestone 1

Implementation follows [the canonical specification](docs/VERYJUDGE_M1_SPEC.md).
Work is gated in ticket order: no later ticket begins before the current
ticket's acceptance tests pass.

## Current state

VJ-001–VJ-014 are complete. The deterministic suite has 492 passing tests on
Docker Postgres 15.19; seven capture-dependent gold checks are skipped until
the human-only VJ-015 records its source cassettes. No VJ-015 work has been
attempted.

## Local setup

Requires Python 3.9+ and Docker with Compose.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[test]'
docker compose up -d --wait postgres
export OPERATOR_TOKEN='replace-with-private-operator-token'
export VIEWER_TOKEN='replace-with-different-private-viewer-token'
.venv/bin/alembic upgrade head
.venv/bin/uvicorn veryjudge.app:app --host 127.0.0.1
```

Render the local Jinja operator page separately, then open the output in a
browser. This keeps the API surface at the six specified JSON endpoints plus
the VJ-001 health check.

```sh
.venv/bin/python -m veryjudge.ui --output operator.html
```

The config requires two nonempty, distinct tokens. Environment values override
`config.yaml`. No token or public endpoint is generated implicitly. `/health`
is the VJ-001 acceptance endpoint and requires the operator bearer token.
Automatic API documentation routes are disabled.

```sh
curl -H "Authorization: Bearer $OPERATOR_TOKEN" http://127.0.0.1:8000/health
```

## Acceptance tests

Use a disposable Postgres 15 database. The migration test deliberately fails
if its database is unavailable; it does not silently substitute SQLite or
skip the ticket gate.

```sh
export TEST_DATABASE_URL='postgresql+psycopg://veryjudge:veryjudge@127.0.0.1:5432/veryjudge'
.venv/bin/python -m pytest
```

The schema has eight domain tables plus Alembic's own migration bookkeeping
table. `StubLLM` validates canned scenario responses without network access.
Tests marked `live` are excluded by default.

## Resolved specification inconsistencies

See [SPEC_QUESTIONS.md](docs/SPEC_QUESTIONS.md) for the two explicit user
resolutions: route-C conflict handling and lifecycle finalization.

VJ-015 is a human task. No real-source fixture preparation or capture has
been performed.

## Minimal Vercel deployment

The repository now exposes the public M1 flow at `/` and keeps the original
operator and viewer JSON endpoints token-protected. The public `POST /api/check`
runs research synchronously so work is not abandoned when a Vercel invocation
returns. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the required hosted
Postgres migration and environment setup.
