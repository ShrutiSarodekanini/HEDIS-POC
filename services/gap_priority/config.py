"""Settings and priority rules for gap prioritization.

Every number the scoring uses lives in priority_rules.yaml so the business can tune it without a code change.
The file is checked when it is loaded: a missing or out-of-range value stops the run before anything is scored.
"""

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

TIERS = ("high", "medium", "low")


@dataclass(frozen=True)
class Settings:
    rules_path: Path              # the business tunes the scoring in this file
    timezone: str                 # encounter times become dates in this zone, so a run repeats exactly anywhere
    actor: str                    # recorded in audit events


def load_settings() -> Settings:
    return Settings(
        rules_path=Path(os.environ.get("GAP_PRIORITY_RULES") or Path(__file__).with_name("priority_rules.yaml")),
        timezone=os.environ.get("GAP_PRIORITY_TIMEZONE", "UTC"),
        actor=os.environ.get("GAP_PRIORITY_ACTOR", "gap_priority"),
    )


SETTINGS = load_settings()


@dataclass(frozen=True)
class Rules:
    version: str
    # closure likelihood: starting point, adjustments and clamp
    default_closure_rate: float
    evidence_waiting: float
    visit_soon: float
    recent_contact: float
    attributed_practice: float
    no_contact: float
    out_of_time: float
    likelihood_min: float
    likelihood_max: float
    # windows, in whole days from the run date
    visit_soon_days: int
    recent_contact_days: int
    no_contact_days: int
    deadline_days: int
    # engagement levels
    engagement_recent: float
    engagement_within_year: float
    engagement_other: float
    engagement_visit_booked: float
    engagement_cap: float
    # financial value
    value_divisor: float
    value_cap: float
    non_star_value: float
    star_year: int
    # tiers
    high_share: Decimal           # Decimal so the tier boundaries round exactly
    medium_share: Decimal
    evidence_floor: str
    # channels
    chart_review_measures: tuple[str, ...]
    mailed_fit_kit_measures: tuple[str, ...]


def load_rules(path: Path = SETTINGS.rules_path) -> Rules:
    """Read and check priority_rules.yaml."""
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    f = _RulesFile(doc, Path(path).name)
    rules = Rules(
        version=f.text("version"),
        default_closure_rate=f.number("likelihood.default_closure_rate", 0, 1),
        evidence_waiting=f.number("likelihood.evidence_waiting", 0, 1),
        visit_soon=f.number("likelihood.visit_soon", 0, 1),
        recent_contact=f.number("likelihood.recent_contact", 0, 1),
        attributed_practice=f.number("likelihood.attributed_practice", 0, 1),
        no_contact=f.number("likelihood.no_contact", -1, 0),
        out_of_time=f.number("likelihood.out_of_time", -1, 0),
        likelihood_min=f.number("likelihood.min", 0, 1),
        likelihood_max=f.number("likelihood.max", 0, 1),
        visit_soon_days=f.whole("windows.visit_soon_days", 1, 3660),
        recent_contact_days=f.whole("windows.recent_contact_days", 1, 3660),
        no_contact_days=f.whole("windows.no_contact_days", 1, 3660),
        deadline_days=f.whole("windows.deadline_days", 1, 3660),
        engagement_recent=f.number("engagement.recent", 0, 1),
        engagement_within_year=f.number("engagement.within_year", 0, 1),
        engagement_other=f.number("engagement.other", 0, 1),
        engagement_visit_booked=f.number("engagement.visit_booked", 0, 1),
        engagement_cap=f.number("engagement.cap", 0, 1),
        value_divisor=f.number("value.divisor", 0.001, 1000),
        value_cap=f.number("value.cap", 0, 1),
        non_star_value=f.number("value.non_star", 0, 1),
        star_year=f.whole("value.star_year", 2000, 2100),
        high_share=Decimal(str(f.number("tiers.high_share", 0, 1))),
        medium_share=Decimal(str(f.number("tiers.medium_share", 0, 1))),
        evidence_floor=f.choice("tiers.evidence_floor", TIERS),
        chart_review_measures=f.codes("channels.chart_review_measures"),
        mailed_fit_kit_measures=f.codes("channels.mailed_fit_kit_measures"),
    )
    _check_together(rules, f.source)
    return rules


def _check_together(rules: Rules, source: str) -> None:
    """Values that are each in range but contradict each other."""
    if rules.likelihood_min >= rules.likelihood_max:
        raise ValueError(f"{source}: likelihood.min must be below likelihood.max")
    if rules.recent_contact_days >= rules.no_contact_days:
        raise ValueError(f"{source}: windows.recent_contact_days must be shorter than windows.no_contact_days")
    if not rules.engagement_other <= rules.engagement_within_year <= rules.engagement_recent:
        raise ValueError(f"{source}: engagement levels must rise from other to within_year to recent")
    if rules.high_share + rules.medium_share > 1:
        raise ValueError(f"{source}: tiers.high_share plus tiers.medium_share must not exceed 1")


class _RulesFile:
    """Typed, range-checked access to the parsed YAML, naming the file and key in every error."""

    def __init__(self, doc: Any, source: str) -> None:
        if not isinstance(doc, dict):
            raise ValueError(f"{source}: expected sections of key: value pairs")
        self.doc = doc
        self.source = source

    def _get(self, key: str) -> Any:
        node: Any = self.doc
        for part in key.split("."):
            if not isinstance(node, dict) or node.get(part) is None:
                raise ValueError(f"{self.source}: {key} is missing")
            node = node[part]
        return node

    def number(self, key: str, low: float, high: float) -> float:
        value = self._get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{self.source}: {key} must be a number, not {value!r}")
        if not low <= value <= high:
            raise ValueError(f"{self.source}: {key} is {value}, outside {low} to {high}")
        return float(value)

    def whole(self, key: str, low: int, high: int) -> int:
        value = self._get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{self.source}: {key} must be a whole number, not {value!r}")
        if not low <= value <= high:
            raise ValueError(f"{self.source}: {key} is {value}, outside {low} to {high}")
        return value

    def text(self, key: str) -> str:
        value = self._get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{self.source}: {key} must be text")
        return value.strip()

    def choice(self, key: str, allowed: tuple[str, ...]) -> str:
        value = self.text(key)
        if value not in allowed:
            raise ValueError(f"{self.source}: {key} is {value!r}, expected one of {', '.join(allowed)}")
        return value

    def codes(self, key: str) -> tuple[str, ...]:
        value = self._get(key)
        if not isinstance(value, list) or not all(isinstance(code, str) and code for code in value):
            raise ValueError(f"{self.source}: {key} must be a list of measure codes")
        return tuple(value)
