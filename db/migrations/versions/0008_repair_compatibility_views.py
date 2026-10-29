"""Repair the compatibility views measures and care_gaps in the active app schema.

The integration stars and what-if bridge reads `measures` and `care_gaps` (see 0005).
Some environments ended up on revision 0007 without those relations in the active schema,
which makes /api/measures fail with UndefinedTable. This migration recreates both as
schema-local views over the team tables.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-27
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_DROP_RELATION = r"""
DO $$
DECLARE
    relkind "char";
BEGIN
    SELECT c.relkind INTO relkind
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = current_schema()
       AND c.relname = '__NAME__';

    IF relkind IS NULL THEN
        RETURN;
    END IF;

    IF relkind = 'v' THEN
        EXECUTE format('DROP VIEW %I.__NAME__', current_schema());
    ELSIF relkind = 'm' THEN
        EXECUTE format('DROP MATERIALIZED VIEW %I.__NAME__', current_schema());
    ELSE
        EXECUTE format('DROP TABLE %I.__NAME__', current_schema());
    END IF;
END$$;
"""

_CREATE_VIEWS = r"""
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


def _drop_relation(name: str) -> None:
    op.execute(_DROP_RELATION.replace("__NAME__", name))


def upgrade() -> None:
    _drop_relation("care_gaps")
    _drop_relation("measures")
    op.execute(_CREATE_VIEWS)


def downgrade() -> None:
    _drop_relation("care_gaps")
    _drop_relation("measures")