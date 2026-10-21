"""Initial schema: reference data, members, engine gaps, documents, evidence review and audit.

The approval rules live in the database, so they hold even if application code is wrong:
- only ACCEPTED evidence candidates can become supplemental records (composite foreign key);
- a candidate's status can only move PROPOSED -> ACCEPTED or REJECTED, and decided candidates are frozen;
- candidates are never deleted, so rejected evidence stays available for audit;
- review decisions, supplemental records, audit events and exports cannot be updated or deleted;
- a rejection needs a reason;
- a candidate's excerpt must match its page text exactly at the stored offsets;
- the same quote cannot be proposed twice for the same gap, including after a rejection.

Revision ID: 0001
Revises:
Create Date: 2026-10-14
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
-- Reference data ---------------------------------------------------------------

CREATE TABLE organizations (
    id            text PRIMARY KEY,
    name          text NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('pcp','specialist','lab','hospital','dialysis','hie','portal',
                                                'urgent_care','hospice','pharmacy','other')),
    city          text,
    state         text,
    closure_rate  numeric(4,3) CHECK (closure_rate BETWEEN 0 AND 1)
);

CREATE TABLE providers (
    id         text PRIMARY KEY,
    name       text NOT NULL,
    org_id     text NOT NULL REFERENCES organizations(id),
    specialty  text
);

CREATE TABLE measures (
    code                    text NOT NULL,
    measurement_year        int  NOT NULL,
    name                    text NOT NULL,
    kind                    text NOT NULL CHECK (kind IN ('member_numerator','two_part','exclusion_focused','episode_inverse')),
    rule_version            text NOT NULL,
    population              jsonb NOT NULL,
    windows                 jsonb NOT NULL,
    thresholds              jsonb,
    most_recent_counts      boolean NOT NULL DEFAULT false,
    allowed_evidence_types  jsonb NOT NULL,
    ai_role                 text,
    rule_text               jsonb NOT NULL,
    prompt_template         text NOT NULL,
    prompt_placeholders     jsonb NOT NULL,
    verify_before_use       jsonb,
    sme_review              jsonb,
    PRIMARY KEY (code, measurement_year)
);

CREATE TABLE measure_star_info (
    measure_code       text NOT NULL,
    measurement_year   int  NOT NULL,
    star_year          int  NOT NULL,
    star_measure_id    text,
    weight             int,
    measure_type       text,
    higher_is_better   boolean,
    scoring_note       text,
    cut_points         jsonb,
    min_rate_for_star  jsonb,
    source_ref         text,
    confidence         text,
    PRIMARY KEY (measure_code, measurement_year, star_year),
    FOREIGN KEY (measure_code, measurement_year) REFERENCES measures(code, measurement_year)
);

-- Members ----------------------------------------------------------------------

CREATE TABLE members (
    member_id           text PRIMARY KEY,
    synthea_patient_id  uuid UNIQUE,
    first_name          text NOT NULL,
    last_name           text NOT NULL,
    sex                 text NOT NULL CHECK (sex IN ('M','F')),
    birth_date          date NOT NULL,
    city                text,
    state               text,
    product_line        text CHECK (product_line IN ('commercial','medicare','medicaid')),
    pcp_org_id          text REFERENCES organizations(id)
);

CREATE TABLE member_engagement (
    member_id          text PRIMARY KEY REFERENCES members(member_id),
    next_visit_date    date,
    last_contact_date  date,
    attributed_org_id  text REFERENCES organizations(id),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

-- Engine output ----------------------------------------------------------------

CREATE TABLE engine_runs (
    run_id                text PRIMARY KEY,
    engine                text NOT NULL,
    engine_version        text NOT NULL,
    measure_spec_version  text NOT NULL,
    measurement_year      int  NOT NULL,
    run_at                timestamptz NOT NULL,
    imported_at           timestamptz NOT NULL DEFAULT now(),
    source_file           text
);

CREATE TABLE measure_summaries (
    run_id            text NOT NULL REFERENCES engine_runs(run_id),
    measure_code      text NOT NULL,
    measurement_year  int  NOT NULL,
    denominator       int  NOT NULL CHECK (denominator >= 0),
    numerator         int  NOT NULL CHECK (numerator >= 0),
    exclusions        int  NOT NULL DEFAULT 0 CHECK (exclusions >= 0),
    rate              numeric(6,4),
    PRIMARY KEY (run_id, measure_code),
    FOREIGN KEY (measure_code, measurement_year) REFERENCES measures(code, measurement_year)
);

CREATE TABLE gaps (
    gap_id               text PRIMARY KEY,
    run_id               text NOT NULL REFERENCES engine_runs(run_id),
    member_id            text NOT NULL REFERENCES members(member_id),
    measure_code         text NOT NULL,
    measurement_year     int  NOT NULL,
    gap_type             text NOT NULL CHECK (gap_type IN ('numerator','episode')),
    missing_element      text NOT NULL,
    last_event           jsonb,
    deadline             date NOT NULL,
    exclusion_check      jsonb,
    episode              jsonb,
    attributed_org_id    text REFERENCES organizations(id),
    status               text NOT NULL DEFAULT 'open'
                         CHECK (status IN ('open','pending_review','partially_evidenced','closed','excluded')),
    pending_count        int  NOT NULL DEFAULT 0,
    closure_probability  numeric(4,3),
    priority_score       numeric(8,4),
    priority_level       text CHECK (priority_level IN ('high','medium','low')),
    priority_reasons     jsonb,
    recommended_action   text,
    action_reason        text,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (measure_code, measurement_year) REFERENCES measures(code, measurement_year),
    CHECK (gap_type <> 'episode' OR episode IS NOT NULL)
);
CREATE INDEX gaps_measure_status_priority_idx ON gaps (measure_code, status, priority_score DESC);
CREATE INDEX gaps_member_idx ON gaps (member_id);

-- Files and documents ----------------------------------------------------------

CREATE TABLE source_files (
    id                    bigserial PRIMARY KEY,
    sha256                char(64) NOT NULL UNIQUE,
    original_name         text NOT NULL,
    stored_path           text NOT NULL,
    detected_type         text,
    size_bytes            bigint,
    received_at           timestamptz NOT NULL DEFAULT now(),
    source_org_id         text REFERENCES organizations(id),
    member_id             text REFERENCES members(member_id),
    match_status          text NOT NULL DEFAULT 'matched' CHECK (match_status IN ('matched','needs_match','unmatched')),
    processing_status     text NOT NULL DEFAULT 'pending'
                          CHECK (processing_status IN ('pending','processing','done','failed','rejected','duplicate')),
    processing_note       text
);

CREATE TABLE documents (
    id                  text PRIMARY KEY,
    source_file_id      bigint NOT NULL REFERENCES source_files(id),
    member_id           text REFERENCES members(member_id),
    doc_type            text,
    document_date       date,
    source_org_id       text REFERENCES organizations(id),
    author_provider_id  text REFERENCES providers(id),
    near_duplicate_of   text REFERENCES documents(id),
    page_count          int,
    conversion_status   text NOT NULL DEFAULT 'pending'
                        CHECK (conversion_status IN ('pending','converted','failed','manual_review')),
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX documents_member_date_idx ON documents (member_id, document_date);

CREATE TABLE document_pages (
    id             bigserial PRIMARY KEY,
    document_id    text NOT NULL REFERENCES documents(id),
    page_no        int  NOT NULL CHECK (page_no >= 1),
    text           text NOT NULL,
    method         text NOT NULL CHECK (method IN ('plain_text','text_layer','ocr_printed','ocr_handwriting','vision')),
    confidence     numeric(4,3),
    image_path     text,
    handwritten    boolean NOT NULL DEFAULT false,
    low_confidence boolean NOT NULL DEFAULT false,
    UNIQUE (document_id, page_no)
);

CREATE TABLE page_words (
    id          bigserial PRIMARY KEY,
    page_id     bigint NOT NULL REFERENCES document_pages(id),
    word_index  int NOT NULL,
    text        text NOT NULL,
    char_start  int,
    char_end    int,
    x           numeric, y numeric, width numeric, height numeric,
    confidence  numeric(4,3),
    UNIQUE (page_id, word_index)
);

CREATE TABLE structured_records (
    id              bigserial PRIMARY KEY,
    source_file_id  bigint NOT NULL REFERENCES source_files(id),
    row_number      int NOT NULL,
    member_id       text REFERENCES members(member_id),
    record_type     text,
    code_system     text,
    code            text,
    description     text,
    value_text      text,
    value_num       numeric,
    unit            text,
    observed_on     date,
    status          text NOT NULL CHECK (status IN ('loaded','rejected','free_text_to_ai')),
    reject_reason   text,
    raw             jsonb NOT NULL,
    UNIQUE (source_file_id, row_number),
    CHECK (status <> 'rejected' OR reject_reason IS NOT NULL)
);
CREATE INDEX structured_records_member_idx ON structured_records (member_id);

-- AI extraction ----------------------------------------------------------------

CREATE TABLE extraction_runs (
    id           bigserial PRIMARY KEY,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    status       text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed','failed','cancelled')),
    provider     text NOT NULL,
    model_name   text NOT NULL,
    scope        jsonb
);

CREATE TABLE extraction_attempts (
    id              bigserial PRIMARY KEY,
    run_id          bigint NOT NULL REFERENCES extraction_runs(id),
    gap_id          text NOT NULL REFERENCES gaps(gap_id),
    status          text NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued','running','succeeded','no_evidence','failed')),
    error           text,
    prompt_version  text,
    pages_sent      jsonb,
    dropped         jsonb,
    duration_ms     int,
    prompt_tokens   int,
    output_tokens   int,
    raw_response    text,
    claimed_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'failed' OR error IS NOT NULL)
);

CREATE TABLE evidence_candidates (
    id                 bigserial PRIMARY KEY,
    gap_id             text NOT NULL REFERENCES gaps(gap_id),
    attempt_id         bigint REFERENCES extraction_attempts(id),
    document_id        text NOT NULL REFERENCES documents(id),
    page_no            int  NOT NULL,
    excerpt            text NOT NULL,
    char_start         int  NOT NULL CHECK (char_start >= 0),
    char_end           int  NOT NULL,
    word_boxes         jsonb,
    evidence_kind      text NOT NULL CHECK (evidence_kind IN ('numerator','exclusion')),
    evidence_type      text NOT NULL,
    service_date_raw   text,
    service_date_from  date,
    service_date_to    date,
    value              text,
    confidence         numeric(4,3),
    reason             text,
    model_name         text,
    prompt_version     text,
    handwritten        boolean NOT NULL DEFAULT false,
    low_confidence     boolean NOT NULL DEFAULT false,
    status             text NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED','ACCEPTED','REJECTED')),
    created_at         timestamptz NOT NULL DEFAULT now(),
    CHECK (char_end > char_start),
    UNIQUE (id, status),
    UNIQUE (gap_id, document_id, page_no, char_start, char_end),
    FOREIGN KEY (document_id, page_no) REFERENCES document_pages(document_id, page_no)
);
CREATE INDEX evidence_candidates_gap_status_idx ON evidence_candidates (gap_id, status);

-- Human review -----------------------------------------------------------------

CREATE TABLE review_decisions (
    id                  bigserial PRIMARY KEY,
    candidate_id        bigint NOT NULL UNIQUE REFERENCES evidence_candidates(id),
    reviewer            text NOT NULL,
    decision            text NOT NULL CHECK (decision IN ('ACCEPTED','REJECTED')),
    reject_reason_code  text CHECK (reject_reason_code IN ('not_this_patient','not_completed','outside_window','value_not_met',
                                                           'approximate_or_undated','quote_not_in_document','unreadable','other')),
    reason              text,
    decided_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, candidate_id),
    CHECK (decision <> 'REJECTED' OR length(trim(coalesce(reason, ''))) > 0)
);

CREATE TABLE supplemental_records (
    id                 bigserial PRIMARY KEY,
    candidate_id       bigint NOT NULL UNIQUE,
    candidate_status   text NOT NULL DEFAULT 'ACCEPTED' CHECK (candidate_status = 'ACCEPTED'),
    decision_id        bigint NOT NULL UNIQUE,
    member_id          text NOT NULL REFERENCES members(member_id),
    measure_code       text NOT NULL,
    measurement_year   int  NOT NULL,
    evidence_kind      text NOT NULL CHECK (evidence_kind IN ('numerator','exclusion')),
    evidence_type      text NOT NULL,
    service_date_from  date,
    service_date_to    date,
    value              text,
    document_id        text NOT NULL REFERENCES documents(id),
    page_no            int  NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (candidate_id, candidate_status) REFERENCES evidence_candidates(id, status),
    FOREIGN KEY (decision_id, candidate_id) REFERENCES review_decisions(id, candidate_id),
    FOREIGN KEY (measure_code, measurement_year) REFERENCES measures(code, measurement_year)
);

-- Audit and export -------------------------------------------------------------

CREATE TABLE audit_events (
    id           bigserial PRIMARY KEY,
    entity_type  text NOT NULL,
    entity_id    text NOT NULL,
    action       text NOT NULL,
    actor        text NOT NULL,
    payload      jsonb,
    at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_events_entity_idx ON audit_events (entity_type, entity_id);

CREATE TABLE export_runs (
    id           bigserial PRIMARY KEY,
    actor        text NOT NULL,
    run_at       timestamptz NOT NULL DEFAULT now(),
    filter       jsonb,
    row_count    int NOT NULL,
    file_sha256  char(64) NOT NULL,
    file_path    text
);

CREATE TABLE export_run_items (
    export_run_id           bigint NOT NULL REFERENCES export_runs(id),
    supplemental_record_id  bigint NOT NULL REFERENCES supplemental_records(id),
    PRIMARY KEY (export_run_id, supplemental_record_id)
);

-- Rules enforced by triggers ---------------------------------------------------

CREATE FUNCTION forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% rows cannot be changed or deleted (table %)', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END $$;

CREATE TRIGGER review_decisions_immutable BEFORE UPDATE OR DELETE ON review_decisions
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER supplemental_records_immutable BEFORE UPDATE OR DELETE ON supplemental_records
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER audit_events_immutable BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER export_runs_immutable BEFORE UPDATE OR DELETE ON export_runs
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER export_run_items_immutable BEFORE UPDATE OR DELETE ON export_run_items
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER evidence_candidates_no_delete BEFORE DELETE ON evidence_candidates
    FOR EACH ROW EXECUTE FUNCTION forbid_change();

CREATE FUNCTION candidate_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status <> 'PROPOSED' THEN
        RAISE EXCEPTION 'candidate % is already %; decided candidates cannot change', OLD.id, OLD.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.status NOT IN ('PROPOSED','ACCEPTED','REJECTED') THEN
        RAISE EXCEPTION 'invalid status %', NEW.status USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.gap_id, NEW.document_id, NEW.page_no, NEW.excerpt, NEW.char_start, NEW.char_end, NEW.evidence_kind)
       IS DISTINCT FROM
       (OLD.gap_id, OLD.document_id, OLD.page_no, OLD.excerpt, OLD.char_start, OLD.char_end, OLD.evidence_kind) THEN
        RAISE EXCEPTION 'candidate % evidence fields cannot change', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER evidence_candidates_guard BEFORE UPDATE ON evidence_candidates
    FOR EACH ROW EXECUTE FUNCTION candidate_guard();

CREATE FUNCTION candidate_excerpt_matches_page() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    page_text text;
BEGIN
    SELECT text INTO page_text FROM document_pages
     WHERE document_id = NEW.document_id AND page_no = NEW.page_no;
    IF page_text IS NULL THEN
        RAISE EXCEPTION 'page % of document % does not exist', NEW.page_no, NEW.document_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF substring(page_text FROM NEW.char_start + 1 FOR NEW.char_end - NEW.char_start) IS DISTINCT FROM NEW.excerpt THEN
        RAISE EXCEPTION 'excerpt does not match page text at % to %', NEW.char_start, NEW.char_end
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER evidence_candidates_excerpt_check BEFORE INSERT ON evidence_candidates
    FOR EACH ROW EXECUTE FUNCTION candidate_excerpt_matches_page();

CREATE FUNCTION supplemental_requires_accept_decision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM review_decisions WHERE id = NEW.decision_id AND decision = 'ACCEPTED') THEN
        RAISE EXCEPTION 'supplemental record needs an ACCEPTED review decision'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER supplemental_records_accept_check BEFORE INSERT ON supplemental_records
    FOR EACH ROW EXECUTE FUNCTION supplemental_requires_accept_decision();
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS export_run_items, export_runs, audit_events, supplemental_records, review_decisions,
    evidence_candidates, extraction_attempts, extraction_runs, structured_records, page_words, document_pages,
    documents, source_files, gaps, measure_summaries, engine_runs, member_engagement, members,
    measure_star_info, measures, providers, organizations CASCADE;
DROP FUNCTION IF EXISTS forbid_change(), candidate_guard(), candidate_excerpt_matches_page(),
    supplemental_requires_accept_decision();
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
