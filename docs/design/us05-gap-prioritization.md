# US-05 gap prioritization: design

Every open gap gets a priority so the team works on the gaps that matter most first. A scoring run reads the gap,
the member's encounters, pending evidence and the measure's star position, and writes one row per gap to the team
table `gap_priority_score` (append-only) plus our `gap_priority_reason`. Screens read the latest score through
`v_gap_priority_latest`.

## In one line

**Priority = how likely the gap closes x how much closing it helps the star rating**, shown as High, Medium or Low
with a short reason and one next action.

## Inputs (all from the aligned schema)

| Input | Source |
|---|---|
| Gaps to score | `care_gap` with `status = 'open'` and `care_gap_detail.review_status` not `closed` or `excluded` |
| Practice track record | `organization_quality.closure_rate` of `care_gap_detail.attributed_org_id` (0.5 when missing) |
| Evidence waiting for review | any `evidence_proposal` for the gap with `status = 'proposed'` |
| Next visit | earliest `encounter` of the patient with `status = 'planned'` on or after the run date |
| Last contact | latest `encounter` of the patient with `status = 'finished'` on or before the run date |
| Days left | `care_gap.deadline_date` minus the run date |
| Measure rate | numerator and denominator from `v_measure_result_latest` |
| Star position | `star_cutpoint` (2027 cut points as the prior-year proxy) and `measure_star_weight.weight` |

The run date is a parameter (default today), so a run can be repeated exactly in tests.

## The three scores (team columns)

### closure_likelihood_score (0 to 1)

Start from the practice's closure rate, then add or subtract:

| Signal | Change |
|---|---|
| Evidence waiting for review | +0.40 |
| Visit booked within 30 days | +0.20 |
| Contacted within the last 90 days | +0.10 |
| Has an attributed practice | +0.05 |
| No contact for over 365 days, or never | -0.15 |
| Fewer than 30 days left and no visit before the deadline | -0.10 |

Clamped to 0.02 to 0.98: the system is never certain.

### engagement_score (0 to 1)

1.0 if contacted within 90 days, 0.6 within 365 days, 0.2 otherwise; +0.2 if any visit is booked; capped at 1.0.

### financial_value_score (0 to 1)

How much one closure helps the star rating.

1. Measure rate = numerator / denominator, rounded to a whole percent the way CMS rounds (half up).
2. Stars now = the highest star whose `rate_low_pct` the rounded rate reaches.
3. Closures needed for the next star = ceil((next cut point - 0.5) / 100 x denominator) - numerator.
4. **Protect a star:** if losing one numerator member would drop a star (the rate sits on the cut point, like COL-E
   today), treat the measure as needing 1 closure.
5. Value = weight / closures needed / 3, capped at 1. A weight-3 measure needing 1 closure scores 1.0.
6. Measures not in Stars (CCS-E, BPD, SPD, AAB), and measures already at 5 stars that are not on a cut point, get a
   small fixed value of 0.05 (they still count for NCQA ratings).

Today's values from the seed:

| Measure | Weight | Needed | Value |
|---|---|---|---|
| KED | 1 | 1 | 0.33 |
| COL-E | 1 | protect (1) | 0.33 |
| GSD | 3 | 7 | 0.14 |
| EED | 1 | 4 | 0.08 |
| CBP | 3 | 14 | 0.07 |
| BCS-E | 1 | 6 | 0.06 |
| CCS-E, BPD, SPD, AAB | n/a | n/a | 0.05 |

## Composite and tier

- `composite_score` = likelihood x value.
- **Tier by rank:** sort all scored gaps by composite (then likelihood, then gap code). Top 20% are `high`, the next
  30% `medium`, the rest `low`.
- **Floor:** a gap with evidence waiting for review is at least `medium` (the cheapest closure there is).
- **Recalculated every run:** values come from the current rates, so once a measure reaches its next star the
  attention moves to the next best measure automatically.

## Next action (recommended_channel)

The first rule that fits:

| Rule | recommended_channel |
|---|---|
| Evidence waiting for review | `abstractor_review` |
| AAB gap (only an exclusion found in the chart can change it) | `chart_review` |
| Visit booked within 30 days | `point_of_care_alert` |
| COL-E and no visit within 30 days | `mailed_fit_kit` |
| No contact for over 365 days | `member_outreach_call` |
| Otherwise | `provider_gap_list` |

## Reasons (our table gap_priority_reason)

`reasons` is a JSON list of short phrases, one per factor that changed the score, value first:
`["Worth a whole star: 1 closure needed", "Visit in 12 days", "Contacted 20 days ago"]`.
`action_reason` is one sentence explaining the action: "A visit is booked in 12 days; the doctor can do the test."
No names, birth dates or document text, only facts about the gap.

## Rules in a file, not in code

All numbers above (adjustments, windows, tier shares, fixed values, the evidence floor) live in
`services/gap_priority/priority_rules.yaml`, so the business can tune them without a code change. Each run records
the rules version it used in the audit event.

## Package

`services/gap_priority/`

| Module | Job |
|---|---|
| `config.py` | Settings and loading `priority_rules.yaml` |
| `inputs.py` | Reads the inputs above in a few set-based queries (no query per gap) |
| `scoring.py` | Pure functions: likelihood, engagement, value, composite, tier, channel, reasons |
| `runner.py` | One run: read, score, insert `gap_priority_score` + `gap_priority_reason` for every gap in one transaction, one audit event `priority.scored` |
| `cli.py` | `python -m services.gap_priority.cli score [--as-of 2026-10-20]` and `show [--tier high] [--measure KED]` |

## Out of scope for this task

- The dashboard screen and its API (US-05 T4).
- Rate and star projection ranges (US-05 T3); this task only needs the current star position.
- Learning the weights from history: rules first; recalibrate once a measurement year of real closures exists.
