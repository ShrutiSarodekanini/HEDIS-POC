"""Star modeler: star ratings from the database star_cutpoint data, and the command-line wrapper
that runs the measure engine's reports mode. Both kept as their authors wrote them."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

CMS_RATING_YEAR = 2027
CMS_TECHNICAL_NOTES_URL = "https://www.cms.gov/files/document/2027-tech-notes-2026-09-30.pdf"
CMS_MEASURE_SET_URL = "https://www.cms.gov/files/document/2027-star-ratings-measures.pdf"


@dataclass(frozen=True)
class StarRatingResult:
	"""Assigned star rating and metadata used by API responses."""

	measure_code: str
	measurement_year: int
	cms_rating_year: int
	input_scoring_value: float | int | None
	assigned_star_rating: int | None
	rating_status: str
	explanation: str
	applied_cut_points: tuple[dict[str, Any], ...]
	applied_scoring_value: float | None

	def to_dict(self) -> dict[str, Any]:
		return {
			"measure_code": self.measure_code,
			"measurement_year": self.measurement_year,
			"cms_rating_year": self.cms_rating_year,
			"input_scoring_value": self.input_scoring_value,
			"applied_scoring_value": self.applied_scoring_value,
			"assigned_star_rating": self.assigned_star_rating,
			"rating_status": self.rating_status,
			"explanation": self.explanation,
			"scoring_direction": "higher_is_better",
			"applied_cut_points": list(self.applied_cut_points),
			"methodology_version": "CMS-DB-star_cutpoint",
			"source_url": CMS_TECHNICAL_NOTES_URL,
			"calculation_version": None,
			"specification_version": None,
		}


def _unavailable(
	*,
	measure_code: str,
	measurement_year: int,
	scoring_value: float | int | None,
	explanation: str,
	cut_points: list[tuple[int, float]] | None = None,
) -> StarRatingResult:
	applied = tuple(
		{"stars": stars, "minimum_value": minimum_value}
		for stars, minimum_value in (cut_points or [])
	)
	return StarRatingResult(
		measure_code=measure_code,
		measurement_year=measurement_year,
		cms_rating_year=CMS_RATING_YEAR,
		input_scoring_value=scoring_value,
		assigned_star_rating=None,
		rating_status="unavailable",
		explanation=explanation,
		applied_cut_points=applied,
		applied_scoring_value=None,
	)


def assign_star_from_cutpoints(
	*,
	measure_code: str,
	measurement_year: int,
	scoring_value: float | int | None,
	denominator: int | None,
	cut_points: list[tuple[int, float]],
) -> StarRatingResult:
	"""Assign a star rating from explicit cut points from the database."""
	code = measure_code.strip().upper()
	if not code:
		return _unavailable(
			measure_code=code,
			measurement_year=measurement_year,
			scoring_value=scoring_value,
			explanation="Measure code is required.",
			cut_points=cut_points,
		)
	if denominator is None or denominator <= 0:
		return _unavailable(
			measure_code=code,
			measurement_year=measurement_year,
			scoring_value=scoring_value,
			explanation="Denominator must be greater than zero.",
			cut_points=cut_points,
		)
	if scoring_value is None:
		return _unavailable(
			measure_code=code,
			measurement_year=measurement_year,
			scoring_value=scoring_value,
			explanation="Scoring value is required.",
			cut_points=cut_points,
		)
	if not cut_points:
		return _unavailable(
			measure_code=code,
			measurement_year=measurement_year,
			scoring_value=scoring_value,
			explanation="No star cut points found in the database for this measure/year.",
		)

	try:
		score = float(Decimal(str(scoring_value)))
	except (InvalidOperation, ValueError):
		return _unavailable(
			measure_code=code,
			measurement_year=measurement_year,
			scoring_value=scoring_value,
			explanation="Scoring value must be numeric.",
			cut_points=cut_points,
		)

	if score < 0 or score > 100:
		return _unavailable(
			measure_code=code,
			measurement_year=measurement_year,
			scoring_value=scoring_value,
			explanation="Scoring value must be between 0 and 100.",
			cut_points=cut_points,
		)

	assigned = None
	for stars, minimum in sorted(cut_points, key=lambda item: item[0]):
		if score >= minimum:
			assigned = stars

	if assigned is None:
		assigned = 1

	applied = tuple(
		{"stars": stars, "minimum_value": minimum}
		for stars, minimum in sorted(cut_points, key=lambda item: item[0])
	)
	return StarRatingResult(
		measure_code=code,
		measurement_year=measurement_year,
		cms_rating_year=CMS_RATING_YEAR,
		input_scoring_value=scoring_value,
		assigned_star_rating=assigned,
		rating_status="assigned",
		explanation="Assigned using star_cutpoint thresholds from the database.",
		applied_cut_points=applied,
		applied_scoring_value=score,
	)


def load_cutpoints_sqlalchemy(
	session: Session,
	*,
	measure_code: str,
	measurement_year: int,
) -> list[tuple[int, float]]:
	"""Read measure cut points from star_cutpoint with a SQLAlchemy session."""
	rows = session.execute(
		text(
			"""SELECT star_rating, rate_low_pct
				 FROM star_cutpoint
				WHERE measure_id = :measure_id
				  AND measurement_year = :measurement_year
				  AND rate_low_pct IS NOT NULL
				ORDER BY star_rating"""
		),
		{"measure_id": measure_code.upper(), "measurement_year": measurement_year},
	).all()
	return [(int(row[0]), float(row[1])) for row in rows]


def assign_star_from_sqlalchemy(
	session: Session,
	*,
	measure_code: str,
	measurement_year: int,
	scoring_value: float | int | None,
	denominator: int | None,
) -> StarRatingResult:
	"""Assign star rating using cut points loaded from the database via SQLAlchemy."""
	cut_points = load_cutpoints_sqlalchemy(
		session, measure_code=measure_code, measurement_year=measurement_year
	)
	return assign_star_from_cutpoints(
		measure_code=measure_code,
		measurement_year=measurement_year,
		scoring_value=scoring_value,
		denominator=denominator,
		cut_points=cut_points,
	)


def has_cutpoints_sqlalchemy(
	session: Session,
	*,
	measure_code: str,
	measurement_year: int,
) -> bool:
	"""Return whether cut points are present for this measure and year."""
	return bool(
		load_cutpoints_sqlalchemy(
			session, measure_code=measure_code, measurement_year=measurement_year
		)
	)


# Command-line wrapper for the measure engine's reports mode.
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

NPM_BIN = "npm.cmd" if sys.platform.startswith("win") else "npm"


def run_star_modeler(args: list[str]) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    cmd = [NPM_BIN, "run", "backend:cli", "--", "reports", *args]
    completed = subprocess.run(cmd, cwd=repo_root)
    return completed.returncode


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print(
            "Usage: python services/star_modeler/main.py -- --measure-bundle <bundle> --patients-directory <dir>"
        )
        sys.exit(0)

    passthrough = sys.argv[1:]
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]
    sys.exit(run_star_modeler(passthrough))
