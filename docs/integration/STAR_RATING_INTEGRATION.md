# Star rating, rate and what-if code in the integration

Branch `ui-integration`, schema `ui20`, screens app on port 8785. Nothing is committed or pushed. No file of
the star rating code, the ingestion code or the lead's was edited, and no table was changed: the only schema change is two new read-only
views (migration 0005).

## The functions now used, and where

All calls go through one bridge file, `services/integration/stars.py`.

| The function | File | Used for | Where it shows |
|---|---|---|---|
| `calculate_rate` | `common/measure_performance.py` | Measure rate (percent, 2 decimals); exclusion projections | Dashboard cards, `/api/measures`, impact block |
| `compare_measure` | `common/measure_performance.py` | What-if: the measure's reviewer-confirmed closures (`projection`), and one gap (`gap_impact`) | Projected rate and star on the cards, "Impact if confirmed" on Evidence Review, `star_scenario` rows |
| `assign_star_from_sqlalchemy` | `services/star_modeler/main.py` | Star for the current rate and for exclusion projections | Cards, impact block |
| `load_cutpoints_sqlalchemy` | `services/star_modeler/main.py` | Cut points loaded once per gap | "Closures still needed for the next star" |
| `assign_star_from_cutpoints` | `services/star_modeler/main.py` | Star for each extra closure counted | "Closures still needed for the next star" |

- `services/integration/work.py` `measures()`: rate, star, cut points, projected rate and projected star now come
  from `stars.measure_card` and `stars.projection`; the response fields the screens use are the same, plus
  `"projection_source": "star_rating.compare_measure"`. Our own rate maths and projection were removed from it.
- `GET /api/gaps/{gap_id}` adds `impact` (`stars.gap_impact`): rate now and after, change in points, stars now
  and after, `star_changed`, closures still needed for the next star, and the exclusion projection.
- `services/integration/evidence.py`: the `star_scenario` snapshot after each decision is filled from
  `stars.projection` (baseline and low = the baseline; high = the projection; closures assumed = the confirmed
  closures he was asked to close).
- The `common/database.py` is not used: its `validate()` checks the alembic revision. `stars.py` builds its own
  SQLAlchemy engine on the app's `DATABASE_URL` (which carries the schema search path), and its sessions only read.
- Unchanged on purpose: the gap prioritization score (`services/gap_priority`) and the page's score breakdown still
  round the rate to a whole percent (US-05 design), and still use their own copy of the cut points.

## The two views (migration `0005_star_views.py`)

`compare_measure` and `get_measure_catalog` read `measures` and `care_gaps`, tables of its own data model that the
team schema does not have. The model matches ours, so the views give the queries those names over the team tables:

| View | Columns | Built from |
|---|---|---|
| `measures` | `measure_id`, `name`, `measurement_year` | `measure_definition`, name from `measure_rule` |
| `care_gaps` | `care_gap_id`, `member_id`, `measure_id`, `measurement_year`, `description`, `status` | One row per patient in `v_measure_result_latest` in the denominator and not excluded. Id = the `care_gap` uuid when that patient has a gap for the measure, else `'met-'` plus the result key. `status` = `closed` when the engine counts the patient met, else `open`. `description` = the gap's missing element, or `Met`. |

Gaps a reviewer confirmed (`closed_pending_engine`) stay `open` in `care_gaps`: they are what the what-if selects.
There is no `members` view (the dashboard query falls back to the team tables without it). The downgrade drops both
views; upgrade, downgrade and upgrade were run on `ui20`. On the current data the views give the same counts as the
cards (for example COL-E 8 closed of 17, KED 5 of 9).

Note: the queries pick the measure by `measure_id` only, not by year. With one measurement year (2026) that is
exact; with two years in the same schema the `care_gaps` count would mix them.

## Exclusion limitation

The what-if only adds closures; it has no case for a patient leaving the denominator. For gaps in
`excluded_pending_engine`, `stars.projection` takes the projected numerator and computes the rate with the
`calculate_rate(numerator, denominator - excluded)` and the star with the `assign_star_from_sqlalchemy`. The impact
block shows the same for an exclusion finding, with the note "The what-if has no exclusion case yet". When the
what-if gains an exclusion case, these two places should call it instead.

## Rounding differences found

The rate keeps 2 decimals and the star is taken from that value; our scoring rounds half up to a whole percent
first. Compared per measure on `ui20`:

| Measure | Before (our code): rate / star / projected | After (the functions): rate / star / projected | Star from whole-percent rate | Different star |
|---|---|---|---|---|
| BCS-E | 0.0 / 1 / 0.0, 1 | 0.0 / 1 / 0.0, 1 | 1 | no |
| CBP | 28.6 / 1 / 28.6, 1 | 28.57 / 1 / 28.57, 1 | 1 | no |
| COL-E | 47.1 / 1 / 52.9, 1 | 47.06 / 1 / 52.94, 1 | 1 | no |
| EED | 66.7 / 2 / 66.7, 2 | 66.67 / 2 / 66.67, 2 | 2 | no |
| GSD | 55.6 / 1 / 55.6, 1 | 55.56 / 1 / 55.56, 1 | 1 | no |
| KED | 55.6 / 2 / 55.6, 2 | 55.56 / 2 / 55.56, 2 | 2 | no |

The measure cards above agree on the current rates, but **two cases on `ui20` give a different star**, both in the
projections the screens show:

| Case | Counts | The rate and star (2 decimals) | Whole-percent rate and star | Shown on screen |
|---|---|---|---|---|
| KED, the impact of an exclusion | 5 of 8 | 62.5%: 2 stars | 63%: 3 stars | the: 2 stars |
| COL-E, after two more closures | 10 of 17 | 58.82%: 1 star, "2 more closures for the next star" | 59%: 2 stars, "1 more closure" | the: 1 star, 2 more |

A rate within half a point under a cut point (KED 63%, COL-E 59%) gets one star less the way. The screens follow the
way (he owns star rating); the gap prioritization score keeps the whole percent (US-05). Not decided here.

## Run on `ui20`

- Upgraded `ui20` to 0005 and restarted only the 8785 app (detached). The apps on 8780 and 8783 were not touched.
- Impact block, COL-E gap `f48e7755`: rate 47.06% → 52.94% (+5.88 points), stars 1 → 1, 2 more closures for the
  next star; as an exclusion 47.06% → 50.0% (+2.94), stars 1 → 1.
- Impact block, KED gap `50b7ec4b`: rate 55.56% → 66.67% (+11.11 points), stars 2 → 3 (`star_changed` true), 1 more
  closure for the next star; as an exclusion 55.56% → 62.5% (+6.94), stars 2 → 2.
- Confirmed the waiting exclusion finding on COL-E gap `f48e7755` (reviewer `abstractor.check`): the gap went to
  `excluded_pending_engine`, supplemental record 2 was written, and the COL-E card's projection moved from 52.94% to
  56.25% (9 of 16: one confirmed closure added, one confirmed exclusion removed), still 1 star.
  `star_scenario` row: COL-E 2026, baseline 47.06, closures assumed 1, low 47.06 / 1 star, high 56.25 / 1 star.
- Evidence Review, after a KED AI review: each queued finding shows "Impact if confirmed", for example "rate
  55.56% → 66.67% (+11.11 points) · stars 2 → 3 · 1 more closure for the next star", and for the exclusion
  finding "rate 55.56% → 62.50% (+6.94 points) · stars 2 → 2".
- KED had an AI run (run 3, 17:13) that I did not start, probably from the page; it proposed the three KED findings
  now in the queue. My run 4 found the same three and saved none (already proposed).

## Still unused from the code

- The separate server, `app.py` at the repo root, with its own endpoints, among them `/api/automation/process-gaps`.
- `_build_star_dashboard` in that `app.py` (the dashboard; the Dashboard screen here keeps its layout and only takes
  the numbers).
- The mock evidence generator in `common/evidence.py` (mock documents for testing). The integration attaches the
  stored synthetic documents instead.
- `get_measure_catalog` works on the new `measures` view but no screen uses it yet.

## Files changed in this round

| File | Change |
|---|---|
| `db/migrations/versions/0005_star_views.py` | New. Views `measures` and `care_gaps`, downgrade drops them. |
| `services/integration/stars.py` | New. The bridge: `measure_card`, `projection`, `gap_impact`, own SQLAlchemy engine. |
| `services/integration/work.py` | Cards from the functions, `projection_source`, `impact` on a gap; own rate and projection code removed. |
| `services/integration/evidence.py` | `star_scenario` filled from `stars.projection`. |
| `services/integration/ui/redesign/app.js` | "Impact if confirmed" on each queued finding. |
| `docs/integration/STAR_RATING_INTEGRATION.md` | This report. |

Checks: ruff passes on the changed Python files, `node --check` passes on `app.js`, and `phi_check.py` finds
nothing on the changed files and this report.
