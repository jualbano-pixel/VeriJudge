"""Run-scoped persistence. Each write locks/checks the running run first."""
from datetime import datetime, timezone

from veryjudge.db import object_from_row, row_from_object
from threading import RLock
from veryjudge.models import Document, Proposition, RunnerConfig


class RunStopped(Exception):
    pass


class ResearchService:
    def __init__(self, repository, runner_factory):
        self.repository, self.runner_factory = repository, runner_factory
        self.inflight = {}
        self.lock = RLock()

    def timeout(self, connection, run_id):
        with self.lock:
            document = self.inflight.pop(run_id, None)
        if document:
            self.repository.insert(connection, "documents", row_from_object(
                document.model_copy(update={"failure_reason": "timeout"}), self.repository.tables["documents"]))

    def execute(self, run_id):
        repository = self.repository
        with repository.transaction() as conn:
            run = repository.get(conn, "research_runs", run_id)
            row = repository.get(conn, "propositions", run["proposition_id"])
            proposition = object_from_row(Proposition, row)
            submission = repository.get(conn, "submissions", row["submission_id"])
        def persist(kind, value):
            with repository.transaction() as conn:
                current = repository.get(conn, "research_runs", run_id, for_update=True)
                if current["status"] != "running":
                    raise RunStopped("run already finalized")
                if kind == "step_start":
                    table = repository.tables["retrieval_steps"]
                    values = row_from_object(value, table)
                    # -1 is the internal unfinalized count; even []/0 finalizes once.
                    values.update(result_count=-1, results_json=[])
                    repository.insert(conn, "retrieval_steps", values)
                elif kind == "step_complete":
                    table = repository.tables["retrieval_steps"]
                    conn.execute(table.update().where(table.c.id == value.id).values(
                        results_json=[r.model_dump(mode="json") for r in value.results], result_count=value.result_count))
                elif kind in {"document", "evidence"}:
                    if kind == "document":
                        with self.lock:
                            self.inflight.pop(run_id, None)
                    table_name = "documents" if kind == "document" else "evidence"
                    repository.insert(conn, table_name, row_from_object(value, repository.tables[table_name]))
                elif kind == "fetch_start":
                    with self.lock:
                        self.inflight[run_id] = value
                elif kind == "rejected":
                    table = repository.tables["research_runs"]
                    conn.execute(table.update().where(table.c.id == run_id).values(rejected_evidence_count=table.c.rejected_evidence_count+1))
        runner = self.runner_factory(persist)
        config = RunnerConfig(**run["config_json"])
        remaining = config.wall_clock_s - (datetime.now(timezone.utc)-run["started_at"]).total_seconds()
        if remaining <= 0:
            raise RunStopped("deadline reached")
        config = config.model_copy(update={"wall_clock_s": remaining})
        return runner.run(proposition, config, run_id=run_id, submitted_text=submission["text"])
