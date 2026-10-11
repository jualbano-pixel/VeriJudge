CREATE TABLE submissions (
    id text PRIMARY KEY,
    text text NOT NULL,
    text_sha256 text NOT NULL,
    extraction_status text NOT NULL CHECK (extraction_status IN ('ok','failed')),
    extraction_error text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_submissions_text_sha256 ON submissions (text_sha256);

CREATE TABLE propositions (
    id text PRIMARY KEY,
    submission_id text NOT NULL REFERENCES submissions(id),
    ordinal int NOT NULL,
    text text NOT NULL,
    original_text text NOT NULL,
    text_normalized text NOT NULL,
    operator_edited bool NOT NULL DEFAULT false,
    proposition_type text NOT NULL CHECK (proposition_type IN ('factual','opinion','prediction','satire','vague','mixed')),
    type_reason text NOT NULL,
    claim_type text NOT NULL CHECK (claim_type IN ('statistic','law','event','quote','other')),
    time_anchor text,
    entities text[] NOT NULL DEFAULT '{}',
    high_risk_category text CHECK (high_risk_category IN ('health','elections','disaster','private_individual','crime_accusation','listed_company_or_market','physical_safety')),
    high_risk bool GENERATED ALWAYS AS (high_risk_category IS NOT NULL) STORED,
    suggested_material bool NOT NULL DEFAULT false,
    selected bool NOT NULL DEFAULT false,
    extraction_flag bool NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (submission_id, ordinal)
);

CREATE INDEX ix_propositions_submission_id ON propositions (submission_id);

CREATE INDEX ix_propositions_text_normalized ON propositions (text_normalized);

CREATE TABLE research_runs (
    id text PRIMARY KEY,
    proposition_id text NOT NULL REFERENCES propositions(id),
    arm text NOT NULL DEFAULT 'A',
    runner_version text NOT NULL,
    model text NOT NULL,
    config_json jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('running','done','failed','timeout')),
    stop_reason text CHECK (stop_reason IN ('sufficient_primary','contradiction_found','budget_exhausted','timeout','no_candidates')),
    failure_reason text,
    rejected_evidence_count int NOT NULL DEFAULT 0,
    token_in int NOT NULL DEFAULT 0,
    token_out int NOT NULL DEFAULT 0,
    cost_usd numeric(10,5) NOT NULL DEFAULT 0,
    started_at timestamptz NOT NULL,
    finished_at timestamptz
);

CREATE INDEX ix_research_runs_proposition_id ON research_runs (proposition_id);

CREATE TABLE retrieval_steps (
    id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES research_runs(id),
    step_number int NOT NULL,
    provider text NOT NULL,
    record_key text,
    query text NOT NULL,
    language text NOT NULL CHECK (language IN ('en','fil')),
    site_restrict text[],
    results_json jsonb NOT NULL,
    result_count int NOT NULL,
    planner_reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, step_number)
);

CREATE INDEX ix_retrieval_steps_run_id ON retrieval_steps (run_id);

CREATE TABLE documents (
    id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES research_runs(id),
    retrieval_step_id text REFERENCES retrieval_steps(id),
    discovered_from_document_id text REFERENCES documents(id),
    operator_added bool NOT NULL DEFAULT false,
    url text NOT NULL,
    final_url text,
    title text,
    fetch_status text NOT NULL CHECK (fetch_status IN ('ok','failed')),
    failure_reason text,
    http_status int,
    html_path text,
    text text,
    text_sha256 text,
    paragraphs_json jsonb,
    published_at timestamptz,
    fetched_at timestamptz NOT NULL,
    ancestry_role text NOT NULL DEFAULT 'originator' CHECK (ancestry_role IN ('originator','carrier')),
    root_document_id text REFERENCES documents(id),
    CHECK ((fetch_status='ok') = (text IS NOT NULL)),
    CHECK (retrieval_step_id IS NOT NULL OR discovered_from_document_id IS NOT NULL OR operator_added),
    CHECK (root_document_id IS DISTINCT FROM id)
);

CREATE INDEX ix_documents_run_id ON documents (run_id);

CREATE INDEX ix_documents_url ON documents (url);

CREATE TABLE evidence (
    id text PRIMARY KEY,
    proposition_id text NOT NULL REFERENCES propositions(id),
    run_id text NOT NULL REFERENCES research_runs(id),
    document_id text NOT NULL REFERENCES documents(id),
    retrieval_step_id text REFERENCES retrieval_steps(id),
    operator_added bool NOT NULL DEFAULT false,
    locator_type text NOT NULL CHECK (locator_type IN ('text_span')),
    locator_start int NOT NULL,
    locator_end int NOT NULL,
    paragraph_index int NOT NULL,
    excerpt text NOT NULL,
    source_class text NOT NULL CHECK (source_class IN ('primary_record','official_statement','direct_witness','original_reporting','wire','republication','fact_check','reference','user_generated')),
    attests_own_act bool,
    attribution text CHECK (attribution IN ('named_record','named_person','unnamed','none')),
    relationship text NOT NULL CHECK (relationship IN ('supports','contradicts','contextualizes')),
    strength text NOT NULL CHECK (strength IN ('direct','indirect','weak')),
    contradiction_route text CHECK (contradiction_route IN ('A','B','C')),
    impossibility_note text,
    closure_record_key text,
    closure_period text,
    limitations text,
    model_rationale text NOT NULL,
    removed_by_operator bool NOT NULL DEFAULT false,
    extraction_flag bool NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (char_length(excerpt) BETWEEN 25 AND 300),
    CHECK (locator_end > locator_start),
    CHECK (source_class <> 'official_statement' OR attests_own_act IS NOT NULL),
    CHECK (source_class <> 'original_reporting' OR attribution IS NOT NULL),
    CHECK (relationship <> 'contradicts' OR contradiction_route IS NOT NULL),
    CHECK (contradiction_route IS DISTINCT FROM 'B' OR impossibility_note IS NOT NULL),
    CHECK (contradiction_route IS DISTINCT FROM 'C' OR (closure_record_key IS NOT NULL AND closure_period IS NOT NULL))
);

CREATE INDEX ix_evidence_proposition_id ON evidence (proposition_id);

CREATE INDEX ix_evidence_run_id ON evidence (run_id);

CREATE INDEX ix_evidence_document_id ON evidence (document_id);

CREATE TABLE judgments (
    id text PRIMARY KEY,
    proposition_id text NOT NULL REFERENCES propositions(id),
    run_id text UNIQUE REFERENCES research_runs(id),
    superseded_by text REFERENCES judgments(id),
    machine_status text NOT NULL CHECK (machine_status IN ('SUPPORTED','CONTRADICTED','NOT_ESTABLISHED','CONFLICTING','NOT_CHECKABLE','PENDING_REVIEW')),
    machine_route text CHECK (machine_route IN ('A','B','C')),
    provisional_status text,
    basis_evidence_ids text[] NOT NULL DEFAULT '{}',
    rule_trace text[] NOT NULL DEFAULT '{}',
    review_reasons text[] NOT NULL DEFAULT '{}',
    machine_explanation_json jsonb NOT NULL,
    relicensed_status text,
    relicensed_route text,
    review_state text NOT NULL DEFAULT 'draft' CHECK (review_state IN ('draft','approved','rejected')),
    final_status text,
    final_explanation_json jsonb,
    as_of timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_judgments_proposition_id ON judgments (proposition_id);

CREATE TABLE reviews (
    id text PRIMARY KEY,
    judgment_id text NOT NULL REFERENCES judgments(id),
    decision text NOT NULL CHECK (decision IN ('approve','downgrade','reject')),
    status_before text NOT NULL,
    relicensed_status text,
    status_after text,
    removed_evidence_ids text[] NOT NULL DEFAULT '{}',
    flagged_evidence_ids text[] NOT NULL DEFAULT '{}',
    added_evidence_ids text[] NOT NULL DEFAULT '{}',
    ancestry_edits_json jsonb,
    explanation_edited bool NOT NULL,
    proposition_flagged bool NOT NULL DEFAULT false,
    error_class text,
    machine_error_reason text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_reviews_judgment_id ON reviews (judgment_id);

CREATE UNIQUE INDEX uq_selected_proposition ON propositions (submission_id) WHERE selected;

CREATE UNIQUE INDEX uq_noncheckable_judgment ON judgments (proposition_id) WHERE run_id IS NULL;
