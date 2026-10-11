"""Immutable machine/audit content and document provenance/ancestry checks."""
from alembic import op

revision="004"
down_revision="003"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
    CREATE FUNCTION freeze_audit_content() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE allowed text[];
    BEGIN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'audit records cannot be deleted' USING ERRCODE='23514';
        END IF;
        allowed := CASE TG_TABLE_NAME
            WHEN 'documents' THEN ARRAY['ancestry_role','root_document_id']
            WHEN 'evidence' THEN ARRAY['removed_by_operator','extraction_flag']
            WHEN 'judgments' THEN ARRAY['superseded_by','relicensed_status','relicensed_route','review_state','final_status','final_explanation_json']
            ELSE ARRAY[]::text[] END;
        IF TG_TABLE_NAME='reviews' OR (to_jsonb(NEW)-allowed) IS DISTINCT FROM (to_jsonb(OLD)-allowed) THEN
            RAISE EXCEPTION 'machine/audit originals are immutable' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END; $$;
    """)
    for table in ("documents","evidence","judgments","reviews"):
        op.execute(f"CREATE TRIGGER freeze_{table} BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION freeze_audit_content()")
    op.execute("""
    CREATE FUNCTION validate_document_links() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE root documents%ROWTYPE; source documents%ROWTYPE;
    BEGIN
        IF NEW.root_document_id IS NOT NULL THEN
            SELECT * INTO root FROM documents WHERE id=NEW.root_document_id;
            IF root.id IS NULL OR root.run_id<>NEW.run_id OR root.id=NEW.id OR root.ancestry_role='carrier' THEN
                RAISE EXCEPTION 'invalid ancestry root' USING ERRCODE='23514';
            END IF;
        END IF;
        IF NEW.ancestry_role='carrier' AND EXISTS(SELECT 1 FROM documents WHERE root_document_id=NEW.id) THEN
            RAISE EXCEPTION 'carrier cannot be an ancestry root' USING ERRCODE='23514';
        END IF;
        IF NOT NEW.operator_added THEN
            IF NEW.retrieval_step_id IS NOT NULL THEN
                IF NOT EXISTS(SELECT 1 FROM retrieval_steps s,jsonb_array_elements(s.results_json) r
                    WHERE s.id=NEW.retrieval_step_id AND s.run_id=NEW.run_id AND r->>'url'=NEW.url) THEN
                    RAISE EXCEPTION 'URL not present in run search results' USING ERRCODE='23514';
                END IF;
            ELSIF NEW.discovered_from_document_id IS NOT NULL THEN
                SELECT * INTO source FROM documents WHERE id=NEW.discovered_from_document_id;
                IF source.id IS NULL OR source.run_id<>NEW.run_id OR source.fetch_status<>'ok' OR strpos(source.text,NEW.url)=0 THEN
                    RAISE EXCEPTION 'invalid link provenance' USING ERRCODE='23514';
                END IF;
            END IF;
        END IF;
        RETURN NEW;
    END; $$;
    CREATE CONSTRAINT TRIGGER document_links AFTER INSERT OR UPDATE ON documents
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_document_links();
    """)


def downgrade():
    op.execute("DROP TRIGGER document_links ON documents; DROP FUNCTION validate_document_links()")
    for table in ("documents","evidence","judgments","reviews"):
        op.execute(f"DROP TRIGGER freeze_{table} ON {table}")
    op.execute("DROP FUNCTION freeze_audit_content()")
