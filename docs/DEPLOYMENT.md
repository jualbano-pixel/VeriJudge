# VeriJudge v0.1 deployment audit and runbook

## Minimum changes implemented

- **FastAPI/Vercel:** root `app.py` exposes the existing ASGI app at a Vercel-recognized entry point. `vercel.json` configures a 300-second function and excludes development-only files.
- **Hosted Postgres:** `DATABASE_URL` is the only database input in production. Common hosted `postgres://` and `postgresql://` URLs are normalized to the installed psycopg 3 driver. Vercel instances use `NullPool` to avoid multiplying persistent connection pools.
- **Migrations:** the existing PostgreSQL-only Alembic chain remains authoritative and unchanged. Run `alembic upgrade head` against the hosted database as a separate release step; migrations must not run during application startup because concurrent cold starts can race.
- **Secrets:** production settings come from environment variables. `OPERATOR_TOKEN` and `VIEWER_TOKEN` remain distinct private credentials and are never sent to the public browser. `OPENAI_API_KEY` and `BRAVE_SEARCH_API_KEY` are server-side only.
- **Filesystem:** package data (`sources.yaml`, prompts, and templates) is read-only. Live cassettes, source captures, and rejected-candidate logs go under `/tmp/veryjudge` on Vercel. They are diagnostic scratch data, not durable records.
- **Long-running work:** the private operator API preserves its existing local threaded runner. The public endpoint runs the same M1 runner, judgment writer, and deterministic licenser inside the request because post-response threads are not durable in a serverless function.
- **Fetching:** outbound search and source fetches use normal HTTPS from the Python runtime. The existing scheme, redirect, DNS, private-address, and locator checks remain in force. Each source fetch is capped at 20 seconds and the M1 research wall clock remains capped at 180 seconds.
- **UI/assets:** `/` renders a packaged Jinja template with inline packaged JavaScript, avoiding runtime writes or a FastAPI static mount. Original sources open only through validated `http`/`https` links with `noopener noreferrer`.
- **Logging/access:** request metadata, duration, failures, submission IDs, and run IDs go to stdout without claim text, tokens, or provider keys. Existing operator/review routes still require `OPERATOR_TOKEN`; existing approved viewer access still requires `VIEWER_TOKEN`. The public surface is limited to `/` and `POST /api/check` and exposes no review mutation or arbitrary judgment lookup.

## Vercel environment variables

Set these for Production (and separately for Preview if desired):

```text
DATABASE_URL=postgresql://...hosted-postgres-connection...
OPERATOR_TOKEN=<long random secret>
VIEWER_TOKEN=<different long random secret>
OPENAI_API_KEY=<server-side key>
BRAVE_SEARCH_API_KEY=<server-side key>
MODEL=<OpenAI model supporting structured JSON output>
VJ_CASSETTE_MODE=record
```

Do not expose any of these with a browser-visible prefix. Keep the database in the same or nearest available region and require TLS in the provider connection string. Use a provider pooler URL if the provider offers one; the app itself does not retain a Vercel-side connection pool.

## Release sequence

1. Create an empty hosted PostgreSQL database compatible with the existing PostgreSQL 15 schema and triggers.
2. From a trusted release environment with `DATABASE_URL` set, run `python -m alembic upgrade head`.
3. Set the Vercel environment variables above and deploy the repository.
4. Verify `/` loads, then run one ordinary, non-high-risk claim end to end and open every returned source.
5. Verify private operator health separately with `Authorization: Bearer $OPERATOR_TOKEN` on `/health`.

## Residual deployment risks

- The public endpoint is intentionally synchronous. It requires Fluid Compute (or another plan/runtime configuration allowing at least the 180-second M1 wall clock plus extraction and judgment calls). A platform-level timeout can still terminate the request before the application writes a terminal state.
- `/tmp` source captures and cassettes disappear with the function instance. The durable audit record remains the existing Postgres document text, hashes, locators, URLs, retrieval trace, evidence, and judgment rows; byte-for-byte source capture retention would require object storage and is outside this minimal deployment.
- Public traffic can spend search/model budget. Configure Vercel Firewall rate limits, spend alerts, and provider quotas before pointing `verijudge.com` at the deployment. Application accounts, sharing, and dashboards are intentionally absent.
- Some publishers block datacenter IPs, JavaScript-only pages, bots, or long downloads. These remain failed fetch attempts under existing M1 behavior.
- The Python runtime and PDF/HTML extraction dependencies must remain below Vercel's uncompressed function bundle limit. The deployment excludes tests, fixtures, local captures, logs, and docs.
- No production provider smoke test is possible without real hosted secrets. The deterministic suite exercises the evidence and licensing behavior without live calls.
