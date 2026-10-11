"""Freeze fixed inputs immediately and lifecycle outputs at finalization."""
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION freeze_run_lifecycle() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'runs are immutable audit records' USING ERRCODE='23514';
        END IF;
        IF OLD.status <> 'running' OR
           ROW(NEW.id,NEW.proposition_id,NEW.arm,NEW.runner_version,NEW.model,NEW.config_json,NEW.started_at)
           IS DISTINCT FROM
           ROW(OLD.id,OLD.proposition_id,OLD.arm,OLD.runner_version,OLD.model,OLD.config_json,OLD.started_at) THEN
            RAISE EXCEPTION 'run inputs and terminal runs are immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.status <> 'running' AND NEW.finished_at IS NULL THEN
            RAISE EXCEPTION 'terminal run requires finished_at' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END; $$;
    CREATE TRIGGER freeze_run BEFORE UPDATE OR DELETE ON research_runs
        FOR EACH ROW EXECUTE FUNCTION freeze_run_lifecycle();
    """)
    op.execute("""
    CREATE FUNCTION freeze_retrieval_step() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'retrieval steps are immutable audit records' USING ERRCODE='23514';
        END IF;
        PERFORM 1 FROM research_runs WHERE id=OLD.run_id AND status='running' FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'terminal run steps are immutable' USING ERRCODE='23514';
        END IF;
        IF OLD.result_count <> -1 OR NEW.result_count < 0 OR
           ROW(NEW.id,NEW.run_id,NEW.step_number,NEW.provider,NEW.record_key,NEW.query,
               NEW.language,NEW.site_restrict,NEW.planner_reason,NEW.created_at)
           IS DISTINCT FROM
           ROW(OLD.id,OLD.run_id,OLD.step_number,OLD.provider,OLD.record_key,OLD.query,
               OLD.language,OLD.site_restrict,OLD.planner_reason,OLD.created_at) THEN
            RAISE EXCEPTION 'step inputs and finalized results are immutable' USING ERRCODE='23514';
        END IF;
        IF jsonb_typeof(NEW.results_json) <> 'array' OR jsonb_array_length(NEW.results_json) <> NEW.result_count THEN
            RAISE EXCEPTION 'result count must match results' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END; $$;
    CREATE TRIGGER freeze_step BEFORE UPDATE OR DELETE ON retrieval_steps
        FOR EACH ROW EXECUTE FUNCTION freeze_retrieval_step();
    """)


def downgrade():
    op.execute("DROP TRIGGER freeze_step ON retrieval_steps; DROP FUNCTION freeze_retrieval_step()")
    op.execute("DROP TRIGGER freeze_run ON research_runs; DROP FUNCTION freeze_run_lifecycle()")
