"""Align with the team schema: the lead's tables exactly as drawn, our extra tables linked to them.

The team schema (Schema design.drawio) is created as drawn: same tables, columns and types. Value lists and ranges
written next to columns in the diagram become CHECK constraints; "append-only" tables refuse updates and deletes.
MEASURE_DEFINITION shows no columns in the diagram, so it holds only its key here.

Everything our features need that the team schema does not have lives in our own tables, linked one to one by the
team schema's IDs (care_gap_detail, document_conversion, evidence_proposal_detail, gap_priority_reason, measure_rule,
measure_star_weight, organization_quality). Our pipeline tables (files, pages, extraction, review decisions,
supplemental records, audit, exports) stay ours and now point at the team tables.

Pre-alpha: no data is kept. Upgrading drops the 0001/0002 tables; reload the seed afterwards.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-20
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

DROP_OLD = r"""
DROP TABLE IF EXISTS export_run_items, export_runs, audit_events, supplemental_records, review_decisions,
    evidence_candidates, extraction_attempts, extraction_runs, structured_records, page_words, document_pages,
    documents, source_files, gaps, measure_summaries, engine_runs, member_engagement, members, measure_star_info,
    measures, providers, organizations CASCADE;
DROP FUNCTION IF EXISTS forbid_change() CASCADE;
DROP FUNCTION IF EXISTS candidate_guard() CASCADE;
DROP FUNCTION IF EXISTS candidate_excerpt_matches_page() CASCADE;
DROP FUNCTION IF EXISTS supplemental_requires_accept_decision() CASCADE;
"""

# ---------------------------------------------------------------- team schema, as drawn

TEAM_SCHEMA = r"""
-- Layer 1: source data --------------------------------------------------------

CREATE TABLE source_batch (
    batch_id        uuid PRIMARY KEY,
    source_system   text,
    status          text CHECK (status IN ('running','completed','failed')),
    resource_count  int,
    started_at      timestamptz
);

CREATE TABLE organization (
    organization_id  text PRIMARY KEY,
    name             text,
    city             text,
    batch_id         uuid REFERENCES source_batch(batch_id)
);

CREATE TABLE location (
    location_id      text PRIMARY KEY,
    organization_id  text REFERENCES organization(organization_id),
    name             text,
    latitude         numeric,
    longitude        numeric,
    batch_id         uuid REFERENCES source_batch(batch_id)
);

CREATE TABLE practitioner (
    practitioner_id  text PRIMARY KEY,
    npi              text UNIQUE,
    family_name      text,
    active           boolean,
    batch_id         uuid REFERENCES source_batch(batch_id)
);

CREATE TABLE patient (
    patient_id   text PRIMARY KEY,
    mrn          text,
    gender       text,
    birth_date   date,
    race         text,
    ethnicity    text,
    birth_sex    text,
    source_file  text,
    batch_id     uuid REFERENCES source_batch(batch_id)
);

CREATE TABLE coverage (
    coverage_id    text PRIMARY KEY,
    patient_id     text REFERENCES patient(patient_id),
    payer_name     text,
    period_start   date,
    period_end     date,
    source_eob_id  text,
    batch_id       uuid REFERENCES source_batch(batch_id)
);

CREATE TABLE encounter (
    encounter_id      text PRIMARY KEY,
    patient_id        text REFERENCES patient(patient_id),
    status            text,
    class_code        text,
    practitioner_npi  text REFERENCES practitioner(npi),
    organization_id   text REFERENCES organization(organization_id),
    period_start      timestamptz,
    batch_id          uuid REFERENCES source_batch(batch_id)
);

CREATE TABLE condition (
    condition_id         text PRIMARY KEY,
    patient_id           text REFERENCES patient(patient_id),
    encounter_id         text REFERENCES encounter(encounter_id),
    clinical_status      text,
    verification_status  text,
    code                 text,
    onset_date           timestamptz,
    batch_id             uuid REFERENCES source_batch(batch_id)
);

CREATE TABLE observation (
    observation_id      text PRIMARY KEY,
    patient_id          text REFERENCES patient(patient_id),
    encounter_id        text REFERENCES encounter(encounter_id),
    category_code       text,
    code                text,
    value_quantity      numeric,
    effective_datetime  timestamptz,
    batch_id            uuid REFERENCES source_batch(batch_id)
);

CREATE TABLE procedure (
    procedure_id     text PRIMARY KEY,
    patient_id       text REFERENCES patient(patient_id),
    encounter_id     text REFERENCES encounter(encounter_id),
    status           text,
    code             text,
    performed_start  timestamptz,
    batch_id         uuid REFERENCES source_batch(batch_id)
);

CREATE TABLE medication_request (
    medication_request_id  text PRIMARY KEY,
    patient_id             text REFERENCES patient(patient_id),
    encounter_id           text REFERENCES encounter(encounter_id),
    status                 text,
    intent                 text,
    medication_code        text,
    authored_on            timestamptz,
    batch_id               uuid REFERENCES source_batch(batch_id)
);

CREATE TABLE immunization (
    immunization_id      text PRIMARY KEY,
    patient_id           text REFERENCES patient(patient_id),
    encounter_id         text REFERENCES encounter(encounter_id),
    status               text,
    vaccine_code         text,
    occurrence_datetime  timestamptz,
    batch_id             uuid REFERENCES source_batch(batch_id)
);

CREATE TABLE allergy_intolerance (
    allergy_id       text PRIMARY KEY,
    patient_id       text REFERENCES patient(patient_id),
    clinical_status  text,
    code             text,
    criticality      text,
    batch_id         uuid REFERENCES source_batch(batch_id)
);

CREATE TABLE document_reference (
    document_reference_id  text PRIMARY KEY,
    patient_id             text REFERENCES patient(patient_id),
    encounter_id           text REFERENCES encounter(encounter_id),
    status                 text,
    type_code              text,
    content_type           text,
    doc_date               timestamptz,
    batch_id               uuid REFERENCES source_batch(batch_id)
);

CREATE TABLE fhir_resource_raw (
    resource_pk    bigserial PRIMARY KEY,
    resource_type  text,
    resource_id    text,
    patient_id     text,
    raw            jsonb,
    batch_id       uuid REFERENCES source_batch(batch_id),
    UNIQUE (resource_type, resource_id)
);

CREATE TABLE feed_completeness_report (
    report_id         bigserial PRIMARY KEY,
    batch_id          uuid REFERENCES source_batch(batch_id),
    resource_type     text,
    record_count      int,
    measure_relevant  boolean,
    generated_at      timestamptz
);

CREATE TABLE domain_event (
    event_id    bigserial PRIMARY KEY,
    event_type  text,
    batch_id    uuid REFERENCES source_batch(batch_id),
    payload     jsonb,
    published   boolean
);

-- Layer 2: measure definitions (the diagram shows the key only) -----------------

CREATE TABLE measure_definition (
    measure_id        text NOT NULL,
    measurement_year  int  NOT NULL,
    PRIMARY KEY (measure_id, measurement_year)
);

-- Layer 3: measure results (append-only) -----------------------------------------

CREATE TABLE measure_result (
    result_pk               bigserial PRIMARY KEY,
    patient_id              text REFERENCES patient(patient_id),
    measure_id              text,
    measurement_year        int,
    in_denominator          boolean,
    in_numerator            boolean,
    excluded                boolean,
    exclusion_reason        text,
    evidence_resource_refs  jsonb,
    engine_version          text,
    computed_at             timestamptz,
    FOREIGN KEY (measure_id, measurement_year) REFERENCES measure_definition(measure_id, measurement_year)
);

CREATE VIEW v_measure_result_latest AS
SELECT DISTINCT ON (patient_id, measure_id, measurement_year) *
FROM measure_result
ORDER BY patient_id, measure_id, measurement_year, computed_at DESC, result_pk DESC;

CREATE TABLE reconciliation_result (
    reconciliation_pk       bigserial PRIMARY KEY,
    patient_id              text REFERENCES patient(patient_id),
    measure_id              text,
    measurement_year        int,
    our_in_numerator        boolean,
    incumbent_in_numerator  boolean,
    match_flag              boolean GENERATED ALWAYS AS (our_in_numerator = incumbent_in_numerator) STORED,
    variance_notes          text,
    FOREIGN KEY (measure_id, measurement_year) REFERENCES measure_definition(measure_id, measurement_year)
);

-- Layer 4: care gaps ---------------------------------------------------------------

CREATE TABLE care_gap (
    gap_id                           uuid PRIMARY KEY,
    patient_id                       text REFERENCES patient(patient_id),
    measure_id                       text,
    measurement_year                 int,
    status                           text CHECK (status IN ('open','closed')),
    missing_element                  text,
    last_relevant_event_date         date,
    last_relevant_event_description  text,
    deadline_date                    date,
    source_measure_result_pk         bigint REFERENCES measure_result(result_pk),
    opened_at                        timestamptz,
    closed_at                        timestamptz,
    FOREIGN KEY (measure_id, measurement_year) REFERENCES measure_definition(measure_id, measurement_year)
);

-- Evidence, priority, stars, data quality ------------------------------------------

CREATE TABLE evidence_proposal (
    evidence_id                  uuid PRIMARY KEY,
    gap_id                       uuid REFERENCES care_gap(gap_id),
    source_document_reference_id text REFERENCES document_reference(document_reference_id),
    passage_citation             text,
    proposed_finding             text,
    confidence_score             numeric CHECK (confidence_score BETWEEN 0 AND 1),
    extraction_model_version     text,
    status                       text CHECK (status IN ('proposed','confirmed','rejected')),
    reviewer_id                  text,
    decided_at                   timestamptz,
    -- "reviewer_id NULL while proposed, CHECK-enforced" (diagram)
    CHECK ((status = 'proposed') = (reviewer_id IS NULL))
);

CREATE TABLE gap_priority_score (
    score_pk                  bigserial PRIMARY KEY,
    gap_id                    uuid REFERENCES care_gap(gap_id),
    closure_likelihood_score  numeric CHECK (closure_likelihood_score BETWEEN 0 AND 1),
    financial_value_score     numeric CHECK (financial_value_score BETWEEN 0 AND 1),
    engagement_score          numeric CHECK (engagement_score BETWEEN 0 AND 1),
    composite_score           numeric,
    priority_tier             text CHECK (priority_tier IN ('high','medium','low')),
    recommended_channel       text,
    scored_at                 timestamptz
);

CREATE VIEW v_gap_priority_latest AS
SELECT DISTINCT ON (gap_id) *
FROM gap_priority_score
ORDER BY gap_id, scored_at DESC, score_pk DESC;

CREATE TABLE star_cutpoint (
    cutpoint_pk       bigserial PRIMARY KEY,
    measure_id        text,                      -- no FK: looked up, not enforced (diagram)
    measurement_year  int,
    star_rating       smallint CHECK (star_rating BETWEEN 1 AND 5),
    rate_low_pct      numeric,
    rate_high_pct     numeric,
    source            text CHECK (source IN ('cms-published-prior-year','illustrative-example'))
);

CREATE TABLE star_scenario (
    scenario_id                 uuid PRIMARY KEY,
    measure_id                  text,
    measurement_year            int,
    baseline_rate_pct           numeric,
    gaps_closed_assumed         int,
    projected_rate_low_pct      numeric,
    projected_rate_high_pct     numeric,
    projected_star_rating_low   smallint CHECK (projected_star_rating_low BETWEEN 1 AND 5),
    projected_star_rating_high  smallint CHECK (projected_star_rating_high BETWEEN 1 AND 5),
    created_by                  text,
    FOREIGN KEY (measure_id, measurement_year) REFERENCES measure_definition(measure_id, measurement_year)
);

CREATE TABLE data_anomaly (
    anomaly_id     uuid PRIMARY KEY,
    resource_type  text,
    measure_id     text,
    anomaly_type   text CHECK (anomaly_type IN ('feed_dropout','volume_drop','volume_spike','denominator_drop',
                                                'mapping_error','other')),
    severity       text CHECK (severity IN ('low','medium','high','critical')),
    details        jsonb,
    batch_id       uuid REFERENCES source_batch(batch_id),
    detected_at    timestamptz,
    resolved_at    timestamptz
);
"""

# ---------------------------------------------------------------- our tables, linked to the team schema

OUR_SCHEMA = r"""
-- Extensions of team tables (one row per team row) ------------------------------------

CREATE TABLE measure_rule (
    measure_id              text NOT NULL,
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
    PRIMARY KEY (measure_id, measurement_year),
    FOREIGN KEY (measure_id, measurement_year) REFERENCES measure_definition(measure_id, measurement_year)
);

CREATE TABLE measure_star_weight (
    measure_id        text NOT NULL,
    measurement_year  int  NOT NULL,
    star_year         int  NOT NULL,
    star_measure_id   text,
    weight            int,
    measure_type      text,
    higher_is_better  boolean,
    scoring_note      text,
    source_ref        text,
    confidence        text,
    PRIMARY KEY (measure_id, measurement_year, star_year),
    FOREIGN KEY (measure_id, measurement_year) REFERENCES measure_definition(measure_id, measurement_year)
);

CREATE TABLE organization_quality (
    organization_id  text PRIMARY KEY REFERENCES organization(organization_id),
    kind             text,
    state            text,
    closure_rate     numeric(4,3) CHECK (closure_rate BETWEEN 0 AND 1)
);

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

CREATE TABLE care_gap_detail (
    gap_id                uuid PRIMARY KEY REFERENCES care_gap(gap_id),
    seed_code             text UNIQUE,                 -- readable code from the seed data, for example G-0001
    engine_run_id         text REFERENCES engine_runs(run_id),
    gap_type              text NOT NULL CHECK (gap_type IN ('numerator','episode')),
    last_event            jsonb,                       -- the engine's full last event (value, code, source)
    exclusion_check       jsonb,
    episode               jsonb,
    episode_encounter_id  text REFERENCES encounter(encounter_id),   -- the AAB visit
    attributed_org_id     text REFERENCES organization(organization_id),
    review_status         text NOT NULL DEFAULT 'open'
                          CHECK (review_status IN ('open','pending_review','partially_evidenced','closed','excluded')),
    pending_count         int  NOT NULL DEFAULT 0 CHECK (pending_count >= 0),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CHECK (gap_type <> 'episode' OR episode IS NOT NULL)
);
CREATE INDEX care_gap_detail_review_idx ON care_gap_detail (review_status);

CREATE TABLE gap_priority_reason (
    score_pk       bigint PRIMARY KEY REFERENCES gap_priority_score(score_pk),
    reasons        jsonb NOT NULL,
    action_reason  text
);

-- Files and pages ---------------------------------------------------------------------

CREATE TABLE source_files (
    id                 bigserial PRIMARY KEY,
    sha256             char(64) NOT NULL UNIQUE,
    original_name      text NOT NULL,
    stored_path        text NOT NULL,
    detected_type      text,
    detected_kind      text,
    size_bytes         bigint,
    received_at        timestamptz NOT NULL DEFAULT now(),
    source_org_id      text REFERENCES organization(organization_id),
    patient_id         text REFERENCES patient(patient_id),
    match_status       text NOT NULL DEFAULT 'matched' CHECK (match_status IN ('matched','needs_match','unmatched')),
    processing_status  text NOT NULL DEFAULT 'pending'
                       CHECK (processing_status IN ('pending','processing','done','failed','rejected','duplicate')),
    processing_note    text,
    encrypted          boolean NOT NULL DEFAULT false,
    parent_file_id     bigint REFERENCES source_files(id),
    scan_result        text CHECK (scan_result IN ('clean','infected','not_scanned','scan_failed'))
);
CREATE INDEX source_files_status_idx ON source_files (processing_status, match_status);

CREATE TABLE document_conversion (
    document_reference_id   text PRIMARY KEY REFERENCES document_reference(document_reference_id),
    source_file_id          bigint NOT NULL REFERENCES source_files(id),
    source_org_id           text REFERENCES organization(organization_id),
    author_practitioner_id  text REFERENCES practitioner(practitioner_id),
    near_duplicate_of       text REFERENCES document_reference(document_reference_id),
    page_count              int,
    conversion_status       text NOT NULL DEFAULT 'pending'
                            CHECK (conversion_status IN ('pending','converted','failed','manual_review')),
    conversion_note         text,
    created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE document_pages (
    id                     bigserial PRIMARY KEY,
    document_reference_id  text NOT NULL REFERENCES document_reference(document_reference_id),
    page_no                int  NOT NULL CHECK (page_no >= 1),
    text                   text NOT NULL,
    method                 text NOT NULL CHECK (method IN ('plain_text','text_layer','ocr_printed','ocr_handwriting','vision')),
    confidence             numeric(4,3),
    image_path             text,
    handwritten            boolean NOT NULL DEFAULT false,
    low_confidence         boolean NOT NULL DEFAULT false,
    ocr_engine             text,
    UNIQUE (document_reference_id, page_no)
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
    patient_id      text REFERENCES patient(patient_id),
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
CREATE INDEX structured_records_patient_idx ON structured_records (patient_id);

-- AI extraction -------------------------------------------------------------------------

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
    gap_id          uuid NOT NULL REFERENCES care_gap(gap_id),
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

CREATE TABLE evidence_proposal_detail (
    evidence_id            uuid PRIMARY KEY REFERENCES evidence_proposal(evidence_id),
    gap_id                 uuid NOT NULL REFERENCES care_gap(gap_id),
    document_reference_id  text NOT NULL REFERENCES document_reference(document_reference_id),
    attempt_id             bigint REFERENCES extraction_attempts(id),
    page_no                int  NOT NULL,
    char_start             int  NOT NULL CHECK (char_start >= 0),
    char_end               int  NOT NULL,
    word_boxes             jsonb,
    evidence_kind          text NOT NULL CHECK (evidence_kind IN ('numerator','exclusion')),
    evidence_type          text NOT NULL,
    service_date_raw       text,
    service_date_from      date,
    service_date_to        date,
    value                  text,
    reason                 text,
    prompt_version         text,
    handwritten            boolean NOT NULL DEFAULT false,
    low_confidence         boolean NOT NULL DEFAULT false,
    created_at             timestamptz NOT NULL DEFAULT now(),
    CHECK (char_end > char_start),
    UNIQUE (gap_id, document_reference_id, page_no, char_start, char_end),
    FOREIGN KEY (document_reference_id, page_no) REFERENCES document_pages(document_reference_id, page_no)
);
CREATE INDEX evidence_proposal_detail_gap_idx ON evidence_proposal_detail (gap_id);

-- Human review -----------------------------------------------------------------------------

CREATE TABLE review_decisions (
    id                  bigserial PRIMARY KEY,
    evidence_id         uuid NOT NULL UNIQUE REFERENCES evidence_proposal(evidence_id),
    reviewer            text NOT NULL,
    decision            text NOT NULL CHECK (decision IN ('confirmed','rejected')),
    reject_reason_code  text CHECK (reject_reason_code IN ('not_this_patient','not_completed','outside_window','value_not_met',
                                                           'approximate_or_undated','quote_not_in_document','unreadable','other')),
    reason              text,
    decided_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, evidence_id),
    CHECK (decision <> 'rejected' OR length(trim(coalesce(reason, ''))) > 0)
);

CREATE TABLE supplemental_records (
    id                     bigserial PRIMARY KEY,
    evidence_id            uuid   NOT NULL UNIQUE REFERENCES evidence_proposal(evidence_id),
    decision_id            bigint NOT NULL UNIQUE,
    patient_id             text   NOT NULL REFERENCES patient(patient_id),
    measure_id             text   NOT NULL,
    measurement_year       int    NOT NULL,
    evidence_kind          text   NOT NULL CHECK (evidence_kind IN ('numerator','exclusion')),
    evidence_type          text   NOT NULL,
    service_date_from      date,
    service_date_to        date,
    value                  text,
    document_reference_id  text   NOT NULL REFERENCES document_reference(document_reference_id),
    page_no                int    NOT NULL,
    created_at             timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (decision_id, evidence_id) REFERENCES review_decisions(id, evidence_id),
    FOREIGN KEY (measure_id, measurement_year) REFERENCES measure_definition(measure_id, measurement_year)
);

-- Audit and export ----------------------------------------------------------------------------

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
"""

# ---------------------------------------------------------------- rules enforced by triggers

RULES = r"""
CREATE FUNCTION forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% rows cannot be changed or deleted (table %)', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END $$;

-- Append-only and immutable tables.
CREATE TRIGGER measure_result_append_only BEFORE UPDATE OR DELETE ON measure_result
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER gap_priority_score_append_only BEFORE UPDATE OR DELETE ON gap_priority_score
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER gap_priority_reason_immutable BEFORE UPDATE OR DELETE ON gap_priority_reason
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER evidence_proposal_no_delete BEFORE DELETE ON evidence_proposal
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER evidence_proposal_detail_immutable BEFORE UPDATE OR DELETE ON evidence_proposal_detail
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
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

-- A proposal starts as proposed. Its status moves once, to confirmed or rejected, and only together with a matching
-- review decision. The evidence itself never changes.
CREATE FUNCTION evidence_proposal_insert_check() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM 'proposed' THEN
        RAISE EXCEPTION 'a new evidence proposal must start as proposed, not %', NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER evidence_proposal_insert BEFORE INSERT ON evidence_proposal
    FOR EACH ROW EXECUTE FUNCTION evidence_proposal_insert_check();

CREATE FUNCTION evidence_proposal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status <> 'proposed' THEN
        RAISE EXCEPTION 'evidence % is already %; decided evidence cannot change', OLD.evidence_id, OLD.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.gap_id, NEW.source_document_reference_id, NEW.passage_citation, NEW.proposed_finding,
        NEW.confidence_score, NEW.extraction_model_version)
       IS DISTINCT FROM
       (OLD.gap_id, OLD.source_document_reference_id, OLD.passage_citation, OLD.proposed_finding,
        OLD.confidence_score, OLD.extraction_model_version) THEN
        RAISE EXCEPTION 'evidence % fields cannot change', OLD.evidence_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.status <> 'proposed' AND NOT EXISTS (
        SELECT 1 FROM review_decisions d
         WHERE d.evidence_id = NEW.evidence_id AND d.decision = NEW.status AND d.reviewer = NEW.reviewer_id) THEN
        RAISE EXCEPTION 'evidence % can only become % with a matching review decision', NEW.evidence_id, NEW.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER evidence_proposal_status BEFORE UPDATE ON evidence_proposal
    FOR EACH ROW EXECUTE FUNCTION evidence_proposal_guard();

-- The detail belongs to its proposal, and the quoted passage is really on the page at the stored offsets.
CREATE FUNCTION evidence_detail_matches_page() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    proposal  evidence_proposal%ROWTYPE;
    page_text text;
BEGIN
    SELECT * INTO proposal FROM evidence_proposal WHERE evidence_id = NEW.evidence_id;
    IF proposal.gap_id IS DISTINCT FROM NEW.gap_id
       OR proposal.source_document_reference_id IS DISTINCT FROM NEW.document_reference_id THEN
        RAISE EXCEPTION 'detail for evidence % does not match its proposal''s gap or document', NEW.evidence_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT text INTO page_text FROM document_pages
     WHERE document_reference_id = NEW.document_reference_id AND page_no = NEW.page_no;
    IF page_text IS NULL THEN
        RAISE EXCEPTION 'page % of document % does not exist', NEW.page_no, NEW.document_reference_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF substring(page_text FROM NEW.char_start + 1 FOR NEW.char_end - NEW.char_start)
       IS DISTINCT FROM proposal.passage_citation THEN
        RAISE EXCEPTION 'passage does not match page text at % to %', NEW.char_start, NEW.char_end
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER evidence_proposal_detail_check BEFORE INSERT ON evidence_proposal_detail
    FOR EACH ROW EXECUTE FUNCTION evidence_detail_matches_page();

-- Supplemental data only from confirmed evidence with a confirming decision.
CREATE FUNCTION supplemental_requires_confirmed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM review_decisions
                    WHERE id = NEW.decision_id AND evidence_id = NEW.evidence_id AND decision = 'confirmed') THEN
        RAISE EXCEPTION 'supplemental record needs a confirming review decision'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM evidence_proposal WHERE evidence_id = NEW.evidence_id AND status = 'confirmed') THEN
        RAISE EXCEPTION 'supplemental record needs confirmed evidence'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER supplemental_records_confirmed_check BEFORE INSERT ON supplemental_records
    FOR EACH ROW EXECUTE FUNCTION supplemental_requires_confirmed();
"""

DROP_NEW = r"""
DROP VIEW IF EXISTS v_gap_priority_latest, v_measure_result_latest;
DROP TABLE IF EXISTS export_run_items, export_runs, audit_events, supplemental_records, review_decisions,
    evidence_proposal_detail, extraction_attempts, extraction_runs, structured_records, page_words, document_pages,
    document_conversion, source_files, gap_priority_reason, care_gap_detail, engine_runs, organization_quality,
    measure_star_weight, measure_rule, data_anomaly, star_scenario, star_cutpoint, gap_priority_score,
    evidence_proposal, care_gap, reconciliation_result, measure_result, measure_definition, domain_event,
    feed_completeness_report, fhir_resource_raw, document_reference, allergy_intolerance, immunization,
    medication_request, procedure, observation, condition, encounter, coverage, patient, practitioner, location,
    organization, source_batch CASCADE;
DROP FUNCTION IF EXISTS forbid_change() CASCADE;
DROP FUNCTION IF EXISTS evidence_proposal_insert_check() CASCADE;
DROP FUNCTION IF EXISTS evidence_proposal_guard() CASCADE;
DROP FUNCTION IF EXISTS evidence_detail_matches_page() CASCADE;
DROP FUNCTION IF EXISTS supplemental_requires_confirmed() CASCADE;
"""


def upgrade() -> None:
    op.execute(DROP_OLD)
    op.execute(TEAM_SCHEMA)
    op.execute(OUR_SCHEMA)
    op.execute(RULES)


def _previous(name: str):
    """Load an earlier migration module by file name, so downgrade can rebuild exactly what it created."""
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"_migration_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def downgrade() -> None:
    op.execute(DROP_NEW)
    _previous("0001_initial_schema.py").upgrade()
    _previous("0002_document_intake.py").upgrade()
