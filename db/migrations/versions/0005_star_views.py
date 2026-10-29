"""Read-only views for the star rating and what-if code: measures and care_gaps over the team tables.

The compare_measure and get_measure_catalog (common/measure_performance.py) read two tables of its own data model,
measures and care_gaps, that the team schema does not have. These views give the queries those names over the
team tables, so the code runs unchanged:

- measures: one row per measure definition, with the measure's name from measure_rule.
- care_gaps: one row per patient in the latest measure result who is in the denominator and not excluded. The id is
  the care_gap uuid when that patient has a gap for the measure, otherwise 'met-' plus the result's key. status is
  'closed' when the engine counts the patient as met, otherwise 'open'. Gaps a reviewer confirmed
  (closed_pending_engine) stay 'open': they are the ones the what-if is asked to close.

No table changes. No members view: the dashboard query falls back to the team tables without it.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-22
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

VIEWS = r"""
CREATE VIEW measures AS
SELECT d.measure_id, coalesce(r.name, d.measure_id) AS name, d.measurement_year
  FROM measure_definition d
  LEFT JOIN measure_rule r ON r.measure_id = d.measure_id AND r.measurement_year = d.measurement_year;

CREATE VIEW care_gaps AS
SELECT coalesce(g.gap_id::text, 'met-' || m.result_pk) AS care_gap_id,
       m.patient_id AS member_id,
       m.measure_id,
       m.measurement_year,
       coalesce(g.missing_element, 'Met') AS description,
       CASE WHEN m.in_numerator THEN 'closed' ELSE 'open' END AS status
  FROM v_measure_result_latest m
  LEFT JOIN care_gap g ON g.patient_id = m.patient_id AND g.measure_id = m.measure_id
                      AND g.measurement_year = m.measurement_year
 WHERE m.in_denominator AND NOT coalesce(m.excluded, false);
"""


def upgrade() -> None:
    op.execute(VIEWS)


def downgrade() -> None:
    op.execute("DROP VIEW care_gaps")
    op.execute("DROP VIEW measures")
