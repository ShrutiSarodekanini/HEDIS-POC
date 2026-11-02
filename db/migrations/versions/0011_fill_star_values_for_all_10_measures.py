"""Fill star values for all 10 dashboard measures in MY2026.

Revision ID: 0013_fill_star_values_all_10
Revises: 0012_user_approval
Create Date: 2026-11-02
"""

from alembic import op

revision = "0013_fill_star_values_all_10"
down_revision = "0012_user_approval"
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
            src.star_measure_id,
            src.weight,
            src.measure_type,
            src.higher_is_better,
            src.scoring_note,
            src.source_ref,
            src.confidence
          FROM (
              VALUES
                  (
                      'BPD',
                      2026,
                      2027,
                      'C14A',
                      3,
                      'intermediate_outcome',
                      true,
                      'Illustrative demo mapping aligned to blood-pressure control scoring behavior.',
                      'https://www.cms.gov/files/document/2027-tech-notes-2026-09-30.pdf',
                      'low (illustrative demo mapping)'
                  ),
                  (
                      'CCS-E',
                      2026,
                      2027,
                      'C01A',
                      1,
                      'process',
                      true,
                      'Illustrative demo mapping aligned to preventive screening scoring behavior.',
                      'https://www.cms.gov/files/document/2027-tech-notes-2026-09-30.pdf',
                      'low (illustrative demo mapping)'
                  ),
                  (
                      'SPD',
                      2026,
                      2027,
                      'C13A',
                      1,
                      'process',
                      true,
                      'Illustrative demo mapping aligned to medication adherence style process thresholds.',
                      'https://www.cms.gov/files/document/2027-tech-notes-2026-09-30.pdf',
                      'low (illustrative demo mapping)'
                  ),
                  (
                      'AAB',
                      2026,
                      2027,
                      'C11A',
                      1,
                      'process',
                      true,
                      'Illustrative demo mapping aligned to quality process thresholds for dashboard scoring.',
                      'https://www.cms.gov/files/document/2027-tech-notes-2026-09-30.pdf',
                      'low (illustrative demo mapping)'
                  )
          ) AS src(
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
         WHERE EXISTS (
             SELECT 1
               FROM measure_definition d
              WHERE d.measure_id = src.measure_id
                AND d.measurement_year = src.measurement_year
         )
        ON CONFLICT (measure_id, measurement_year, star_year) DO UPDATE
              SET star_measure_id = EXCLUDED.star_measure_id,
                  weight = EXCLUDED.weight,
                  measure_type = EXCLUDED.measure_type,
                  higher_is_better = EXCLUDED.higher_is_better,
                  scoring_note = EXCLUDED.scoring_note,
                  source_ref = EXCLUDED.source_ref,
                  confidence = EXCLUDED.confidence
        """
    )

    op.execute(
        """
        DELETE FROM star_cutpoint
         WHERE measurement_year = 2026
           AND measure_id IN ('BPD', 'CCS-E', 'SPD', 'AAB')
        """
    )

    op.execute(
        """
        INSERT INTO star_cutpoint (
            measure_id,
            measurement_year,
            star_rating,
            rate_low_pct,
            rate_high_pct,
            source
        )
        VALUES
            ('BPD', 2026, 1, 0, 72, 'illustrative-example'),
            ('BPD', 2026, 2, 72, 79, 'illustrative-example'),
            ('BPD', 2026, 3, 79, 84, 'illustrative-example'),
            ('BPD', 2026, 4, 84, 90, 'illustrative-example'),
            ('BPD', 2026, 5, 90, 100, 'illustrative-example'),

            ('CCS-E', 2026, 1, 0, 63, 'illustrative-example'),
            ('CCS-E', 2026, 2, 63, 70, 'illustrative-example'),
            ('CCS-E', 2026, 3, 70, 77, 'illustrative-example'),
            ('CCS-E', 2026, 4, 77, 83, 'illustrative-example'),
            ('CCS-E', 2026, 5, 83, 100, 'illustrative-example'),

            ('SPD', 2026, 1, 0, 46, 'illustrative-example'),
            ('SPD', 2026, 2, 46, 63, 'illustrative-example'),
            ('SPD', 2026, 3, 63, 72, 'illustrative-example'),
            ('SPD', 2026, 4, 72, 83, 'illustrative-example'),
            ('SPD', 2026, 5, 83, 100, 'illustrative-example'),

            ('AAB', 2026, 1, 0, 61, 'illustrative-example'),
            ('AAB', 2026, 2, 61, 72, 'illustrative-example'),
            ('AAB', 2026, 3, 72, 79, 'illustrative-example'),
            ('AAB', 2026, 4, 79, 86, 'illustrative-example'),
            ('AAB', 2026, 5, 86, 100, 'illustrative-example')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM star_cutpoint
         WHERE measurement_year = 2026
           AND source = 'illustrative-example'
           AND measure_id IN ('BPD', 'CCS-E', 'SPD', 'AAB')
        """
    )

    op.execute(
        """
        UPDATE measure_star_weight
           SET star_measure_id = NULL,
               weight = NULL,
               measure_type = 'not_star',
               higher_is_better = NULL,
               scoring_note = CASE measure_id
                   WHEN 'BPD' THEN 'Not a Part C Star Ratings measure; rate only.'
                   WHEN 'CCS-E' THEN 'Not a Part C Star Ratings measure; rate only.'
                   WHEN 'SPD' THEN 'Not a Star measure. Part D SUPD is a different measure; rate only.'
                   WHEN 'AAB' THEN 'Not a Part C Star Ratings measure; episode-based quality measure used for rate tracking only.'
                   ELSE scoring_note
               END,
               source_ref = 'https://www.cms.gov/files/document/2027-star-ratings-measures.pdf',
               confidence = 'high (CMS 2027 measures list)'
         WHERE measurement_year = 2026
           AND star_year = 2027
           AND measure_id IN ('BPD', 'CCS-E', 'SPD', 'AAB')
        """
    )