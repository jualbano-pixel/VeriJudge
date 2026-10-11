"""Enforce cross-table locator invariants without adding tables or columns.

PostgreSQL CHECK constraints cannot reference other tables. The constraint
trigger raises check_violation (23514) for the specified document invariants.
"""
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION validate_evidence_locator() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        d documents%ROWTYPE;
        p propositions%ROWTYPE;
        run_prop text;
        para jsonb;
        paragraph_text text;
    BEGIN
        SELECT * INTO d FROM documents WHERE id = NEW.document_id;
        SELECT proposition_id INTO run_prop FROM research_runs WHERE id = NEW.run_id;
        SELECT * INTO p FROM propositions WHERE id = NEW.proposition_id;
        IF d.id IS NULL OR d.fetch_status <> 'ok' OR d.run_id <> NEW.run_id
           OR run_prop IS DISTINCT FROM NEW.proposition_id THEN
            RAISE EXCEPTION 'evidence must reference successful same-run document and run proposition'
                USING ERRCODE = '23514';
        END IF;
        SELECT value INTO para FROM jsonb_array_elements(d.paragraphs_json)
            WHERE (value->>'index')::int = NEW.paragraph_index;
        IF para IS NULL OR NEW.locator_start < (para->>'start')::int
           OR NEW.locator_end > (para->>'end')::int OR NEW.locator_start < 0
           OR substring(d.text FROM NEW.locator_start + 1 FOR NEW.locator_end - NEW.locator_start)
                IS DISTINCT FROM NEW.excerpt THEN
            RAISE EXCEPTION 'invalid evidence locator' USING ERRCODE = '23514';
        END IF;
        paragraph_text := substring(d.text FROM (para->>'start')::int + 1
                                   FOR (para->>'end')::int - (para->>'start')::int);
        IF NEW.locator_start <> (para->>'start')::int + strpos(paragraph_text, NEW.excerpt) - 1 THEN
            RAISE EXCEPTION 'locator must use first occurrence in stated paragraph' USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM unnest(p.entities) entity
                       WHERE btrim(entity) <> '' AND strpos(lower(paragraph_text), lower(entity)) > 0) THEN
            RAISE EXCEPTION 'excerpt paragraph lacks proposition entity' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;
    """)
    op.execute("""
    CREATE CONSTRAINT TRIGGER evidence_locator_check
    AFTER INSERT OR UPDATE ON evidence
    FOR EACH ROW EXECUTE FUNCTION validate_evidence_locator();
    """)


def downgrade():
    op.execute("DROP TRIGGER evidence_locator_check ON evidence")
    op.execute("DROP FUNCTION validate_evidence_locator()")

