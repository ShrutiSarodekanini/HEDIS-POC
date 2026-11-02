"""Add explicit non-Star metadata rows for measures without CMS star cut points.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-29
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute(
        """
        INSERT INTO measure_star_weight (
            measure_id,
            measurement_year,
            star_year,
            star_measure_id,
            weight,
            measure_type,
            higher_is_better,
            scoring_note,
            source_ref,
            confidence
        )
        SELECT
            src.measure_id,
            src.measurement_year,
            src.star_year,
            NULL,
            NULL,
            'not_star',
            NULL,
            src.scoring_note,
            'https://www.cms.gov/files/document/2027-star-ratings-measures.pdf',
            'high (CMS 2027 measures list)'
          FROM (
              VALUES
                  ('BPD', 2026, 2027, 'Not a Part C Star Ratings measure; rate only.'),
                  ('CCS-E', 2026, 2027, 'Not a Part C Star Ratings measure; rate only.'),
                  ('SPD', 2026, 2027, 'Not a Star measure. Part D SUPD is a different measure; rate only.'),
                  (
                      'AAB',
                      2026,
                      2027,
                      'Not a Part C Star Ratings measure; episode-based quality measure used for rate tracking only.'
                  )
          ) AS src(measure_id, measurement_year, star_year, scoring_note)
         WHERE EXISTS (
             SELECT 1
               FROM measure_definition d
              WHERE d.measure_id = src.measure_id
                AND d.measurement_year = src.measurement_year
         )
        ON CONFLICT (measure_id, measurement_year, star_year) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM measure_star_weight
         WHERE measurement_year = 2026
           AND star_year = 2027
           AND measure_type = 'not_star'
           AND measure_id IN ('BPD', 'CCS-E', 'SPD', 'AAB')
        """
    )
