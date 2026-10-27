-- Fresh, repeatable demo data for the HEDIS POC evidence workflow.
-- Apply after `alembic upgrade head`. Rerunning this script updates the same
-- demo rows and does not delete any existing data.

INSERT INTO members (
    member_id,
    first_name,
    last_name,
    date_of_birth,
    gender
)
VALUES (
    'M1001',
    'Alex',
    'Taylor',
    DATE '1979-04-18',
    'Female'
)
ON CONFLICT (member_id) DO UPDATE SET
    first_name = EXCLUDED.first_name,
    last_name = EXCLUDED.last_name,
    date_of_birth = EXCLUDED.date_of_birth,
    gender = EXCLUDED.gender;

INSERT INTO measures (
    measure_id,
    name,
    description,
    measurement_year
)
VALUES (
    'COL',
    'Colorectal Cancer Screening',
    'Members 45-75 years of age with appropriate colorectal screening.',
    2026
)
ON CONFLICT (measure_id) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    measurement_year = EXCLUDED.measurement_year;

INSERT INTO care_gaps (
    care_gap_id,
    member_id,
    measure_id,
    description,
    status,
    date_identified,
    measurement_year
)
VALUES (
    'CG1001',
    'M1001',
    'COL',
    'No qualifying colorectal cancer screening found in measurement year.',
    'Open',
    DATE '2026-01-12',
    2026
)
ON CONFLICT (care_gap_id) DO UPDATE SET
    member_id = EXCLUDED.member_id,
    measure_id = EXCLUDED.measure_id,
    description = EXCLUDED.description,
    status = EXCLUDED.status,
    date_identified = EXCLUDED.date_identified,
    measurement_year = EXCLUDED.measurement_year;

INSERT INTO synthea_records (
    record_id,
    member_id,
    care_gap_id,
    source_type,
    source_date,
    fact_key,
    fact_value,
    provenance
)
VALUES
    (
        'SR1001',
        'M1001',
        'CG1001',
        'Encounter',
        DATE '2025-08-03',
        'encounter_reason',
        'Annual wellness visit',
        'synthea/encounters.csv'
    ),
    (
        'SR1002',
        'M1001',
        'CG1001',
        'Observation',
        DATE '2025-08-03',
        'bmi',
        '27.1',
        'synthea/observations.csv'
    ),
    (
        'SR1003',
        'M1001',
        'CG1001',
        'CareGapAnalysis',
        DATE '2026-01-12',
        'gap_evidence_summary',
        'No qualifying lab/procedure code for colorectal screening was found.',
        'hedis/gap_identifier'
    )
ON CONFLICT (record_id) DO UPDATE SET
    member_id = EXCLUDED.member_id,
    care_gap_id = EXCLUDED.care_gap_id,
    source_type = EXCLUDED.source_type,
    source_date = EXCLUDED.source_date,
    fact_key = EXCLUDED.fact_key,
    fact_value = EXCLUDED.fact_value,
    provenance = EXCLUDED.provenance;
