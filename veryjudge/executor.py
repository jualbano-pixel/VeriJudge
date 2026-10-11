"""Two runner workers, watchdog finalization, and startup orphan reaping."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from threading import Timer


def reap_orphaned_runs(repository):
    now = datetime.now(timezone.utc)
    with repository.transaction() as conn:
        table = repository.tables["research_runs"]
        runs = conn.execute(table.select().where(table.c.status == "running").with_for_update()).mappings()
        for run in runs:
            if run["started_at"] + timedelta(seconds=run["config_json"].get("wall_clock_s", 180)) <= now:
                conn.execute(table.update().where(table.c.id == run["id"]).values(
                    status="failed", failure_reason="orphaned", finished_at=now))


class RunExecutor:
    def __init__(self, repository, handler, on_complete=None, on_timeout=None):
        self.repository, self.handler = repository, handler
        self.on_complete = on_complete
        self.on_timeout = on_timeout
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="veryjudge")

    def submit(self, run_id):
        with self.repository.transaction() as conn:
            run = self.repository.get(conn, "research_runs", run_id)
        remaining = max(0, run["config_json"].get("wall_clock_s", 180) -
                        (datetime.now(timezone.utc) - run["started_at"]).total_seconds())
        watchdog = Timer(remaining, self._finish, args=(run_id, "timeout", "timeout", None))
        watchdog.daemon = True
        watchdog.start()
        return self.pool.submit(self._execute, run_id, watchdog)

    def execute_inline(self, run_id):
        """Complete a run inside the current request (required by serverless runtimes)."""
        with self.repository.transaction() as conn:
            run = self.repository.get(conn, "research_runs", run_id)
        remaining = max(0, run["config_json"].get("wall_clock_s", 180) -
                        (datetime.now(timezone.utc) - run["started_at"]).total_seconds())
        watchdog = Timer(remaining, self._finish, args=(run_id, "timeout", "timeout", None))
        watchdog.daemon = True
        watchdog.start()
        self._execute(run_id, watchdog)

    def _finish(self, run_id, status, stop_reason=None, failure_reason=None):
        changed = False
        with self.repository.transaction() as conn:
            run = self.repository.get(conn, "research_runs", run_id, for_update=True)
            if run and run["status"] == "running":
                if status == "timeout" and self.on_timeout:
                    self.on_timeout(conn, run_id)
                table = self.repository.tables["research_runs"]
                conn.execute(table.update().where(table.c.id == run_id).values(status=status,
                    stop_reason=stop_reason, failure_reason=failure_reason, finished_at=datetime.now(timezone.utc)))
                changed = True
        if changed and status != "failed" and self.on_complete:
            self.on_complete(run_id)

    def _execute(self, run_id, watchdog):
        try:
            with self.repository.transaction() as conn:
                run = self.repository.get(conn, "research_runs", run_id)
                if run["status"] != "running":
                    return
            trace = self.handler(run_id)
            reason = trace.stop_reason if trace is not None else None
            self._finish(run_id, "timeout" if reason == "timeout" else "done", reason)
        except Exception as error:
            self._finish(run_id, "failed", failure_reason=str(error) or type(error).__name__)
        finally:
            watchdog.cancel()

    def shutdown(self):
        self.pool.shutdown(wait=True)
