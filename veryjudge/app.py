from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import logging
import os
from threading import Lock
import time
import uuid

from fastapi import FastAPI, Header, HTTPException, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import IntegrityError
from veryjudge.models import ReviewRequest

from veryjudge.config import load_settings
from veryjudge.db import Database
from veryjudge.extraction import ClaimExtractor
from veryjudge.executor import RunExecutor, reap_orphaned_runs
from veryjudge.ids import new_id
from veryjudge.llm import OpenAILLM, StubLLM

logger = logging.getLogger("veryjudge")
logger.setLevel(logging.INFO)


def create_app(settings=None, llm=None, database=None, run_handler=None, runner_factory=None,review_fetcher=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings or load_settings()
        app.state.db = database or Database(app.state.settings.database_url)
        app.state.llm = llm or (StubLLM() if app.state.settings.model == "stub" else
            OpenAILLM(app.state.settings.provider_keys.get("llm", ""), app.state.settings.model, app.state.settings.llm_base_url or None))
        app.state.extractor = ClaimExtractor(app.state.llm)
        reap_orphaned_runs(app.state.db)
        app.state.executor = None
        app.state.dispatch_lock = Lock()
        from veryjudge.research import ResearchService
        from veryjudge.runtime import default_runner_factory
        app.state.research = ResearchService(app.state.db, runner_factory or default_runner_factory(app.state.settings, app.state.llm))
        from veryjudge.judgments import JudgmentService
        app.state.judgments = JudgmentService(app.state.db,app.state.llm)
        from veryjudge.review import ReviewService
        from veryjudge.fetcher import DocumentFetcher,RecordedFetcher
        from veryjudge.providers import RecordReplay
        app.state.review=ReviewService(app.state.db,app.state.judgments,review_fetcher or
            RecordedFetcher(DocumentFetcher(os.environ.get("VJ_CAPTURE_DIR", "/tmp/veryjudge/captures" if os.environ.get("VERCEL") else "captures")),
                RecordReplay(os.environ.get("VJ_CASSETTE_DIR", "/tmp/veryjudge/cassettes" if os.environ.get("VERCEL") else "fixtures/cassettes"),
                             os.environ.get("VJ_CASSETTE_MODE","record" if os.environ.get("VERCEL") else "replay"))))
        yield
        if app.state.executor is not None:
            app.state.executor.shutdown()
        if app.state.db is not None and database is None:
            app.state.db.engine.dispose()

    app = FastAPI(title="VeryJudge", version="0.1.0", docs_url=None,
                  redoc_url=None, openapi_url=None, lifespan=lifespan)
    # The Jinja operator page is a local file, preserving the six JSON API routes.
    app.add_middleware(CORSMiddleware,allow_origins=["null"],allow_methods=["GET","POST"],
                       allow_headers=["Authorization","Content-Type"])

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        request_id = request.headers.get("x-vercel-id") or uuid.uuid4().hex
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request_failed request_id=%s method=%s path=%s", request_id, request.method, request.url.path)
            raise
        response.headers["X-Request-ID"] = request_id
        logger.info("request_complete request_id=%s method=%s path=%s status=%s duration_ms=%d",
                    request_id, request.method, request.url.path, response.status_code,
                    round((time.monotonic() - started) * 1000))
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,error):
        return JSONResponse(status_code=400,content={"detail":"Invalid request"})

    def principal(authorization: str = Header(default="")):
        for role,token in (("operator",app.state.settings.OPERATOR_TOKEN),("viewer",app.state.settings.VIEWER_TOKEN)):
            if hmac.compare_digest(authorization.encode(),("Bearer "+token).encode()):
                return role
        raise HTTPException(404)

    def operator(role=Depends(principal)):
        if role!="operator":
            raise HTTPException(status_code=404)

    def db():
        if app.state.db is None:
            app.state.db = Database(app.state.settings.database_url)
        return app.state.db

    def dispatch_run(run_id):
        with app.state.dispatch_lock:
            if app.state.executor is None:
                app.state.executor = RunExecutor(db(), run_handler or app.state.research.execute,
                    on_timeout=app.state.research.timeout,on_complete=app.state.judgments.complete)
            app.state.executor.submit(run_id)

    def create_submission(raw):
        if not isinstance(raw, str) or not raw.strip():
            raise HTTPException(400, "Paste something first.")
        if len(raw) < 20:
            raise HTTPException(400, "That's too short to check — paste the full sentence.")
        if len(raw) > 1000:
            raise HTTPException(400, "Trim this to the part you want checked (max 1,000 characters).")
        repository = db()
        submission_id, created_at = new_id("sub"), datetime.now(timezone.utc)
        with repository.transaction() as connection:
            repository.insert(connection, "submissions", dict(id=submission_id, text=raw,
                text_sha256=hashlib.sha256(raw.encode()).hexdigest(), extraction_status="failed", created_at=created_at))
        try:
            extracted = app.state.extractor.extract(raw, created_at)
            rows = []
            with repository.transaction() as connection:
                for ordinal, proposition in enumerate(extracted, 1):
                    values = proposition.model_dump(mode="json")
                    values.update(id=new_id("prop"), submission_id=submission_id, ordinal=ordinal,
                                  original_text=proposition.text, text_normalized=" ".join(proposition.text.lower().split()))
                    rows.append(dict(repository.insert(connection, "propositions", values)))
                table = repository.tables["submissions"]
                connection.execute(table.update().where(table.c.id == submission_id).values(extraction_status="ok", extraction_error=None))
        except Exception:
            logger.exception("claim_extraction_failed submission_id=%s", submission_id)
            with repository.transaction() as connection:
                table = repository.tables["submissions"]
                connection.execute(table.update().where(table.c.id == submission_id).values(extraction_error="extraction_failed"))
            raise HTTPException(502, "We couldn't read that. Try again.")
        return dict(submission_id=submission_id, propositions=rows)

    def select_for_run(submission_id, proposition_id):
        repository = db()
        with repository.transaction() as connection:
            submission = repository.get(connection, "submissions", submission_id, for_update=True)
            prop = repository.get(connection, "propositions", proposition_id)
            if not submission or not prop or prop["submission_id"] != submission_id:
                raise HTTPException(404)
            connection.execute(repository.tables["propositions"].update().where(
                repository.tables["propositions"].c.id == proposition_id).values(selected=True))
            now = datetime.now(timezone.utc)
            if prop["proposition_type"] != "factual":
                judgment_id = new_id("jdg")
                repository.insert(connection, "judgments", dict(id=judgment_id, proposition_id=proposition_id,
                    machine_status="NOT_CHECKABLE", rule_trace=["R0 type=" + prop["proposition_type"]],
                    machine_explanation_json=dict(sentences=[dict(text="NOT_CHECKABLE. See the evidence table.", kind="meta", evidence_ids=[])],
                                                  what_would_change=[], limitations=[]), as_of=now))
                return None, judgment_id
            run_id = new_id("run")
            config = app.state.settings
            repository.insert(connection, "research_runs", dict(id=run_id, proposition_id=proposition_id,
                runner_version="0.1.0", model=config.model, status="running", started_at=now,
                config_json={key: getattr(config, key) for key in ("max_searches", "max_captures", "max_fetch_attempts", "wall_clock_s")}))
        return run_id, None

    @app.get("/", response_class=HTMLResponse)
    def public_page():
        from veryjudge.ui import render_template
        return render_template("public.html")

    @app.post("/api/check")
    def public_check(payload: dict):
        submission = create_submission(payload.get("text"))
        propositions = submission["propositions"]
        factual = [row for row in propositions if row["proposition_type"] == "factual"]
        proposition = next((row for row in factual if row["suggested_material"]), None) or (factual[0] if factual else propositions[0])
        run_id, judgment_id = select_for_run(submission["submission_id"], proposition["id"])
        if run_id:
            executor = RunExecutor(db(), run_handler or app.state.research.execute,
                on_timeout=app.state.research.timeout, on_complete=app.state.judgments.complete)
            try:
                executor.execute_inline(run_id)
            finally:
                executor.shutdown()
            with db().transaction() as connection:
                run = db().get(connection, "research_runs", run_id)
                rows = db().rows(connection, "judgments", run_id=run_id)
            if not rows:
                logger.error("public_run_failed run_id=%s reason=%s", run_id, run["failure_reason"])
                raise HTTPException(502, "Research could not be completed. Please try again.")
            judgment_id = rows[0]["id"]
        with db().transaction() as connection:
            judgment = db().get(connection, "judgments", judgment_id)
            return app.state.judgments.full(connection, judgment, viewer=True)

    @app.get("/health", dependencies=[Depends(operator)])
    def health():
        return {"status": "ok"}

    @app.post("/submissions", dependencies=[Depends(operator)])
    def submit(payload: dict):
        return create_submission(payload.get("text"))

    @app.post("/submissions/{submission_id}/propositions/{proposition_id}/select", dependencies=[Depends(operator)])
    def select_proposition(submission_id: str, proposition_id: str, payload: dict):
        if payload.get("single_predicate_confirmed") is not True:
            raise HTTPException(400, "single_predicate_confirmed is required")
        if "edited_text" in payload and not isinstance(payload["edited_text"], str):
            raise HTTPException(400, "edited_text must be text")
        repository = db()
        with repository.transaction() as connection:
            submission = repository.get(connection, "submissions", submission_id, for_update=True)
            prop = repository.get(connection, "propositions", proposition_id)
            if not submission or not prop or prop["submission_id"] != submission_id:
                raise HTTPException(404)
            table = repository.tables["propositions"]
            if connection.execute(table.select().where(table.c.submission_id == submission_id, table.c.selected)).first():
                raise HTTPException(409, "A proposition is already selected")
            changes = {"selected": True}
            if "edited_text" in payload:
                changes.update(text=payload["edited_text"], operator_edited=True,
                               text_normalized=" ".join(payload["edited_text"].lower().split()))
            connection.execute(table.update().where(table.c.id == proposition_id).values(**changes))
            now = datetime.now(timezone.utc)
            if prop["proposition_type"] != "factual":
                judgment_id = new_id("jdg")
                repository.insert(connection, "judgments", dict(id=judgment_id, proposition_id=proposition_id,
                    machine_status="NOT_CHECKABLE", rule_trace=["R0 type=" + prop["proposition_type"]],
                    machine_explanation_json=dict(sentences=[dict(text="NOT_CHECKABLE. See the evidence table.", kind="meta", evidence_ids=[])],
                                                  what_would_change=[], limitations=[]), as_of=now))
                return dict(proposition_id=proposition_id, judgment_id=judgment_id)
            run_id = new_id("run")
            config = app.state.settings
            repository.insert(connection, "research_runs", dict(id=run_id, proposition_id=proposition_id,
                runner_version="0.1.0", model=config.model, status="running", started_at=now,
                config_json={key: getattr(config, key) for key in ("max_searches", "max_captures", "max_fetch_attempts", "wall_clock_s")}))
        dispatch_run(run_id)
        return dict(proposition_id=proposition_id, run_id=run_id)

    @app.get("/runs/{run_id}",dependencies=[Depends(operator)])
    def get_run(run_id:str):
        repository=db()
        with repository.transaction() as conn:
            run=repository.get(conn,"research_runs",run_id)
            if not run:
                raise HTTPException(404)
            steps=repository.rows(conn,"retrieval_steps",run_id=run_id)
            docs=repository.rows(conn,"documents",run_id=run_id)
            judgments=repository.rows(conn,"judgments",run_id=run_id)
        latest=max(steps,key=lambda s:s["step_number"]) if steps else None
        current=(f"Search {latest['step_number']} of 8: {latest['query']} via {latest['provider']}" if latest else "Planning searches")
        if run["status"] in {"done","timeout"}:
            current="Licensing status"
        return dict(status=run["status"],searches_done=len(steps),captures_ok=sum(d["fetch_status"]=="ok" for d in docs),
                    fetch_attempts=len(docs),current_step=current,judgment_id=judgments[0]["id"] if judgments else None,
                    failure_reason=run["failure_reason"],stop_reason=run["stop_reason"])

    @app.post("/runs/{run_id}/retry",dependencies=[Depends(operator)])
    def retry(run_id:str):
        repository=db()
        with repository.transaction() as conn:
            run=repository.get(conn,"research_runs",run_id,for_update=True)
            if not run:
                raise HTTPException(404)
            if run["status"] not in {"failed","timeout"}:
                raise HTTPException(409,"Only failed or timeout runs may be retried")
            rid=new_id("run")
            repository.insert(conn,"research_runs",dict(id=rid,proposition_id=run["proposition_id"],arm=run["arm"],
                runner_version=run["runner_version"],model=run["model"],config_json=run["config_json"],status="running",
                started_at=datetime.now(timezone.utc)))
        dispatch_run(rid)
        return {"run_id":rid}

    @app.get("/judgments/{judgment_id}")
    def get_judgment(judgment_id:str,role=Depends(principal)):
        repository=db()
        with repository.transaction() as conn:
            judgment=repository.get(conn,"judgments",judgment_id)
            if not judgment or (role=="viewer" and (judgment["review_state"]!="approved" or judgment["superseded_by"])):
                raise HTTPException(404)
            return app.state.judgments.full(conn,judgment,viewer=role=="viewer")

    @app.post("/judgments/{judgment_id}/review",dependencies=[Depends(operator)])
    def review(judgment_id:str,payload:dict):
        repository=db()
        try:
            request=ReviewRequest.model_validate(payload)
            with repository.transaction() as conn:
                judgment=repository.get(conn,"judgments",judgment_id,for_update=True)
                if not judgment:
                    raise HTTPException(404)
                if judgment["superseded_by"]:
                    raise HTTPException(409,"Superseded judgments cannot be reviewed")
                review_id=app.state.review.apply(conn,judgment,request)
                updated=repository.get(conn,"judgments",judgment_id)
                result=app.state.judgments.full(conn,updated)
            return dict(review_id=review_id,judgment=result)
        except (ValueError,IntegrityError) as error:
            raise HTTPException(400,str(error) if isinstance(error,ValueError) else "Invalid review evidence or ancestry")

    return app


app = create_app()
