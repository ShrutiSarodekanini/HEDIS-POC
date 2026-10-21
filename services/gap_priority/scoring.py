"""Pure scoring: the three team scores, tier, next action and reasons for each gap. No database access.

Every number comes from the Rules loaded from priority_rules.yaml. The only constants here are CMS rate rounding
(half up to a whole percent) and the wording of reasons, which use facts about the gap only: no names, birth dates
or document text.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .config import TIERS, Rules
from .inputs import GapInput, MeasureInput

ABSTRACTOR_REVIEW = "abstractor_review"
CHART_REVIEW = "chart_review"
POINT_OF_CARE_ALERT = "point_of_care_alert"
MAILED_FIT_KIT = "mailed_fit_kit"
MEMBER_OUTREACH_CALL = "member_outreach_call"
PROVIDER_GAP_LIST = "provider_gap_list"
CHANNELS = (ABSTRACTOR_REVIEW, CHART_REVIEW, POINT_OF_CARE_ALERT, MAILED_FIT_KIT, MEMBER_OUTREACH_CALL,
            PROVIDER_GAP_LIST)


@dataclass(frozen=True)
class Signals:
    """The run-date facts about one gap that several scores and reasons share."""
    days_to_visit: int | None       # to the next booked visit
    days_since_contact: int | None  # since the last finished visit
    days_left: int | None           # to the gap's deadline
    visit_soon: bool                # a visit is booked within the soon window
    recent_contact: bool            # contacted within the recent window
    unreachable: bool               # no contact for longer than the no-contact window, or never
    out_of_time: bool               # close to the deadline with no visit before it


@dataclass(frozen=True)
class MeasureValue:
    """How much one closure helps the measure's star rating, and why, in one phrase."""
    value: float
    reason: str


@dataclass(frozen=True)
class Ranked:
    """What the tier ranking looks at for one gap."""
    key: str
    composite: float
    likelihood: float
    evidence_waiting: bool


def signals(gap: GapInput, as_of: date, rules: Rules) -> Signals:
    days_to_visit = (gap.next_visit - as_of).days if gap.next_visit else None
    days_since_contact = (as_of - gap.last_contact).days if gap.last_contact else None
    days_left = (gap.deadline - as_of).days if gap.deadline else None
    visit_before_deadline = gap.next_visit is not None and (gap.deadline is None or gap.next_visit <= gap.deadline)
    return Signals(
        days_to_visit=days_to_visit,
        days_since_contact=days_since_contact,
        days_left=days_left,
        visit_soon=days_to_visit is not None and days_to_visit <= rules.visit_soon_days,
        recent_contact=days_since_contact is not None and days_since_contact <= rules.recent_contact_days,
        unreachable=days_since_contact is None or days_since_contact > rules.no_contact_days,
        out_of_time=days_left is not None and days_left < rules.deadline_days and not visit_before_deadline,
    )


def likelihood(gap: GapInput, as_of: date, rules: Rules) -> float:
    """closure_likelihood_score: the practice's track record moved by what is known about this gap and member."""
    s = signals(gap, as_of, rules)
    score = gap.closure_rate if gap.closure_rate is not None else rules.default_closure_rate
    score += rules.evidence_waiting if gap.evidence_waiting else 0
    score += rules.visit_soon if s.visit_soon else 0
    score += rules.recent_contact if s.recent_contact else 0
    score += rules.attributed_practice if gap.attributed_org_id else 0
    score += rules.no_contact if s.unreachable else 0
    score += rules.out_of_time if s.out_of_time else 0
    return min(rules.likelihood_max, max(rules.likelihood_min, score))


def engagement(gap: GapInput, as_of: date, rules: Rules) -> float:
    """engagement_score: how reachable the member is, from the last contact and any booked visit."""
    s = signals(gap, as_of, rules)
    if s.recent_contact:
        level = rules.engagement_recent
    elif not s.unreachable:
        level = rules.engagement_within_year
    else:
        level = rules.engagement_other
    if gap.next_visit is not None:
        level += rules.engagement_visit_booked
    return min(rules.engagement_cap, level)


def measure_value(measure: MeasureInput | None, rules: Rules) -> MeasureValue:
    """financial_value_score for every gap of a measure: weight / closures to the next star (or 1 to protect the
    current star) / divisor. Measures outside Stars, without a rate, or safely at 5 stars get the small fixed value."""
    if measure is None or not measure.weight or not measure.cut_points:
        return MeasureValue(rules.non_star_value, "Not a Star measure")
    if measure.denominator == 0:
        return MeasureValue(rules.non_star_value, "No measure rate yet")
    stars_now = _stars(measure, measure.numerator)
    if measure.numerator > 0 and _stars(measure, measure.numerator - 1) < stars_now:
        needed, reason = 1, f"Protects the {_ordinal(stars_now)} star"
    else:
        next_low = dict(measure.cut_points).get(stars_now + 1)
        if next_low is None:
            return MeasureValue(rules.non_star_value, f"Already at {stars_now} stars")
        # The smallest numerator whose half-up rounded rate reaches the next whole-percent cut point.
        target = math.ceil((math.ceil(next_low) - Decimal("0.5")) * measure.denominator / 100)
        needed = target - measure.numerator
        if needed == 1:
            reason = "Worth a whole star: 1 closure needed"
        else:
            reason = f"{needed} closures needed for the {_ordinal(stars_now + 1)} star"
    return MeasureValue(min(rules.value_cap, measure.weight / needed / rules.value_divisor), reason)


def _stars(measure: MeasureInput, numerator: int) -> int:
    """The highest star whose lower cut point the rate reaches, after rounding half up to a whole percent as CMS
    does. Integer arithmetic keeps x.5 exact."""
    rounded = (200 * numerator + measure.denominator) // (2 * measure.denominator)
    return max((star for star, low in measure.cut_points if rounded >= low), default=0)


def tiers(ranked: Sequence[Ranked], rules: Rules) -> list[str]:
    """Tier by rank, in the order given: the top share high, the next share medium, the rest low; then a gap with
    evidence waiting is raised to the evidence floor. Ranking by share keeps the High list short whatever the scale."""
    order = sorted(range(len(ranked)), key=lambda i: (-ranked[i].composite, -ranked[i].likelihood, ranked[i].key))
    high_end = _half_up(len(ranked) * rules.high_share)
    medium_end = _half_up(len(ranked) * (rules.high_share + rules.medium_share))
    floor = TIERS.index(rules.evidence_floor)
    result = [""] * len(ranked)
    for rank, i in enumerate(order):
        tier = 0 if rank < high_end else 1 if rank < medium_end else 2
        if ranked[i].evidence_waiting:
            tier = min(tier, floor)
        result[i] = TIERS[tier]
    return result


def channel(gap: GapInput, as_of: date, rules: Rules) -> str:
    """recommended_channel: the first rule that fits."""
    s = signals(gap, as_of, rules)
    if gap.evidence_waiting:
        return ABSTRACTOR_REVIEW
    if gap.measure_id in rules.chart_review_measures:
        return CHART_REVIEW
    if s.visit_soon:
        return POINT_OF_CARE_ALERT
    if gap.measure_id in rules.mailed_fit_kit_measures:
        return MAILED_FIT_KIT
    if s.unreachable:
        return MEMBER_OUTREACH_CALL
    return PROVIDER_GAP_LIST


def reasons(gap: GapInput, value: MeasureValue, as_of: date, rules: Rules) -> list[str]:
    """One short phrase per factor behind the score, the value first."""
    s = signals(gap, as_of, rules)
    found = [value.reason]
    if gap.evidence_waiting:
        found.append("Evidence waiting for review")
    if s.visit_soon:
        found.append(f"Visit {_in_days(s.days_to_visit)}")
    if s.recent_contact:
        found.append(f"Contacted {_days_ago(s.days_since_contact)}")
    elif s.unreachable:
        found.append("No contact for over a year" if s.days_since_contact is not None else "No recorded contact")
    if s.out_of_time:
        found.append(f"{_days(s.days_left)} left and no visit before the deadline")
    if gap.attributed_org_id is None:
        found.append("No attributed practice")
    elif gap.closure_rate is not None:
        found.append(f"Practice closes {round(gap.closure_rate * 100)}% of gaps")
    return found


def action_reason(gap: GapInput, chosen: str, as_of: date, rules: Rules) -> str:
    """One sentence saying why the recommended channel is the next step."""
    s = signals(gap, as_of, rules)
    if chosen == ABSTRACTOR_REVIEW:
        return "Evidence for this gap is waiting for review; confirming it is the quickest way to close the gap."
    if chosen == CHART_REVIEW:
        return f"Only an exclusion found in the chart can change this {gap.measure_id} gap."
    if chosen == POINT_OF_CARE_ALERT:
        return f"A visit is booked {_in_days(s.days_to_visit)}; the doctor can do the test."
    if chosen == MAILED_FIT_KIT:
        return (f"No visit is booked in the next {rules.visit_soon_days} days; a FIT kit by mail lets the member "
                "screen at home.")
    if chosen == MEMBER_OUTREACH_CALL:
        if s.days_since_contact is None:
            return "There is no recorded contact with the member; a call can bring them in."
        return "There has been no contact for over a year; a call can bring the member back in."
    return (f"No visit is booked in the next {rules.visit_soon_days} days; the practice's gap list keeps it in "
            "front of the care team.")


def _half_up(number: Decimal) -> int:
    return int(number.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _days(n: int | None) -> str:
    return "1 day" if n == 1 else f"{n} days"


def _in_days(n: int | None) -> str:
    return "today" if n == 0 else f"in {_days(n)}"


def _days_ago(n: int | None) -> str:
    return "today" if n == 0 else f"{_days(n)} ago"
