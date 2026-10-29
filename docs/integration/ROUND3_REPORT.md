# Round 3 report (branch ui-integration, schema ui20, port 8785)

No schema change, no change to teammates' files (the ingestion code, the star rating `common/` and `star_modeler`, the
lead's tables), no tests, no commits. Every record below is referred to by internal ID; no names or document text.

## Files changed

| File | Change |
|---|---|
| services/evidence_extraction/contracts.py | PROMPT_VERSION `us03-v2`; `Finding.part` and `Candidate.part`; `part` added to `OUTPUT_SCHEMA` (required, enum) |
| services/evidence_extraction/prompt.py | reason is 2 to 4 sentences (what the page says, why it meets or excludes, any trap line nearby, no guessing); frailty exclusions come as two findings with `"part"` set to `frailty` or `advanced_illness` |
| services/evidence_extraction/validator.py | reason trimmed to 800 characters; `part` read and checked; a frailty-type finding without a part is dropped with a reason |
| services/integration/parts.py (new) | two-part rules: KED eGFR plus urine part, frailty plus advanced illness, `review_status` with `partially_evidenced` |
| services/integration/evidence.py | already-proposed findings recorded in `extraction_attempts.dropped`; part stored with the reason; gap status from `parts` |
| services/integration/export.py | exports only gaps in `closed_pending_engine` or `excluded_pending_engine` |
| services/integration/work.py | gap panel fields; each finding gets `part`, `context`, `rule_check`, `attempt`; `ai_runs` gets pages sent, raw answer, prompt version; measures report ready and waiting-for-parts counts |
| services/integration/pipeline.py | a batch is one zip or a folder of CSVs; CSVs go through the ingestion `upload_dataset` |
| services/integration/app.py | `POST /api/batches` takes `file` (one zip) or `files` (CSVs); refuses empty, mixed, several zips, other types, unsafe names |
| services/integration/measures.py | engine retried once after 5 s when it crashes (negative exit code, 4294967295, or no output at all) |
| services/integration/ui/redesign/index.html, app.js, styles.css | multi-file upload; finding box (GAP, FINDING, WHY, CHECK, QUOTE, IMPACT, AI RUN); gap panel on Evidence Review and on a Prioritization row click |
| docs/integration/STAR_RATING_INTEGRATION.md | rounding note corrected (KED exclusion 5/8 and COL-E 10/17 cases) |

## How the frailty part is stored

There is no column for it and the brief rules out a schema change, so the part is a prefix on
`evidence_proposal_detail.reason`: `[part: frailty] ...` or `[part: advanced_illness] ...`. `parts.tag_reason` writes
it, `parts.split_reason` reads it; the API returns `part` and the reason without the prefix, so reviewers never see
it. KED parts need no storage: the part follows from `evidence_type` (eGFR, or uACR, or urine albumin plus urine
creatinine within `thresholds.albumin_creatinine_max_days_apart` = 4 days). `measure_rule` names the kind
(`two_part`), the windows and that threshold, but not the parts, so the pairing is written in `parts.py`.

Gap status, worked out after every save and decision:

| Situation | review_status | On work list | Exported |
|---|---|---|---|
| nothing proposed | open | yes | no |
| something proposed, nothing confirmed | pending_review | yes | no |
| some parts confirmed, at least one missing | partially_evidenced | yes | no |
| every part of a closure confirmed | closed_pending_engine | no | yes |
| every part of an exclusion confirmed | excluded_pending_engine | no | yes |

## Runs

### Uploads (Start over before each)

| Upload | Batch | Time | Rows read | Resources generated / persisted | Open gaps | Documents attached |
|---|---|---|---|---|---|---|
| one zip, 20 patients | a3af6880 | 17 min | patients 20, encounters 1,645, observations 22,092, procedures 4,329, ... (10 files) | 34,524 / 34,524 | 34 (BCS-E 9, COL-E 9, CBP 5, KED 4, GSD 4, EED 3) | 30 of 30, 0 failed |
| 13 separate CSVs, `files` fields | 65400cdd | 16 min | same row counts | 34,524 / 34,524 | 34, same split | 30 of 30, 0 failed |

The two paths give identical results. The engine retry (5 s, once) did not fire in either run (0 "retrying" lines in
the log).

### Model used

`.env` names `minimax/minimax-m3.1-flash:free`. OpenRouter retired that free variant on 22 Oct 2026 (HTTP 404, paid
variant only) and our key is free tier, so no paid model was used and `.env` was not changed. For the 8785 process only:

| Attempt | Model | Result (stored in extraction_attempts with the reason) |
|---|---|---|
| 1 | minimax/minimax-m3.1-flash:free | failed, HTTP 404 retired |
| 2 | nvidia/nemotron-3-super-120b-a12b:free (code default) | failed, HTTP 404 from the provider |
| 3 | google/gemma-4-31b-it:free | failed, HTTP 429 after 3 waits |
| 4 | nvidia/nemotron-3.5-super-vl:free | failed after 29 min, empty answer |
| 5 | recursive/flint-v1.0:free | failed, answer cut off at 8,192 output tokens |
| 6 onward | recursive/flint-v1.0:free with EXTRACTION_MAX_OUTPUT_TOKENS=32768 | succeeded, 19 to 92 s per gap |

Every failure is a failed attempt with its reason; none became "no evidence".

### Bug found and fixed during the run

The prompt asked for `"part"` but `OUTPUT_SCHEMA` in `contracts.py` (sent as `response_format` json_schema) did not
list it, so a schema-following model left it out and the validator correctly dropped the frailty findings ("needs two
findings, and this one does not say whether it is the frailty or the advanced illness part"; attempts 6 and 7).
`part` is now in the schema, required, enum `""`, `frailty`, `advanced_illness`. After the fix the same gap returned
both parts (attempt 8).

### KED, gap 4e5e6288 (two parts)

| Step | review_status | Ready to export | Waiting for parts | Export |
|---|---|---|---|---|
| AI run 6: eGFR and uACR kept (reasons 3 sentences, about 260 characters) | pending_review | 0 | 0 | |
| confirm eGFR only (decision 1) | partially_evidenced | 0 | 1 | 0 rows |
| confirm uACR (decision 2) | closed_pending_engine | 2 | 0 | export run 1, 2 rows |

The gap left the work list after the second confirmation. KED card: 5/9 = 55.56%, 2 stars; projected 66.67%, 3 stars.

### Frailty exclusion, COL-E gap f48e7755 (two parts)

| Step | review_status | Ready | Waiting | Export |
|---|---|---|---|---|
| AI run 8: frailty (part 1 of 2) and advanced illness (part 2 of 2) kept | pending_review | 0 | 0 | |
| re-run AI (run 9) | pending_review | | | |
| confirm frailty (decision 3) | partially_evidenced | 0 | 1 | |
| confirm advanced illness (decision 4) | excluded_pending_engine | 2 | 0 | export run 2, 2 rows |

Run 9 history, `extraction_attempts.dropped` for attempt 9 (status succeeded, nothing saved, skipped 2):

```
{"evidence_type": "frailty_and_advanced_illness_age_66_plus", "reason": "already proposed in an earlier run",
 "evidence_id": "caa40652-c5a6-43eb-a0ce-6801edfc833e", "already_proposed": true}
{"evidence_type": "frailty_and_advanced_illness_age_66_plus", "reason": "already proposed in an earlier run",
 "evidence_id": "7c74565f-25ac-42ad-aea1-fcaa4d427c69", "already_proposed": true}
```

### COL-E closure, gap 1a171c12 (one part)

AI run 10: colonoscopy kept, confidence 0.95, 19.2 s. Confirmed (decision 5): closed_pending_engine; export run 3,
1 row. COL-E card: 8/17 = 47.06%, 1 star, 1 confirmed closure and 1 confirmed exclusion waiting for the engine;
projected 9/16 = 56.25%, 1 star.

### Finding box, as shown on Evidence Review (gap 1a171c12, before the decision)

Document text and the reason's wording are left out here; on the page they are shown to the reviewer.

```
pt-e5d5... · Colorectal Cancer Screening                                   0.95 confidence
GAP      Colorectal Cancer Screening · the measure engine found no qualifying event in 2026 · deadline 31 Dec 2026 · Medium
FINDING  Colonoscopy on 22 Dec 2023 (no polyps). This counts for Colorectal Cancer Screening, so the gap can be
         closed once you confirm it.
WHY      [3 sentences from the model: what the page says, why it is inside the colonoscopy window, and which nearby
         lines (a family history and a planned future colonoscopy) do not count] (the model's explanation, not a fact)
CHECK    Window 1 Jan 2017 to 31 Dec 2026 (MY-9 to MY): service date 22 Dec 2023 is inside
QUOTE    [about 200 characters of page text either side, the quote highlighted]  U-6, page 2: view the page image
IMPACT   Impact if confirmed: rate 47.06% -> 52.94% (+5.88 points) · stars 1 -> 1 · 2 more closures for the next star
AI RUN   recursive/flint-v1.0:free · 23 Oct 2026 01:23:50 · 19.2 s · 1 kept, 0 dropped
```

Backend fields for the KED findings (gap 4e5e6288): `rule_check` part 1 of 2 and part 2 of 2, window MY
(2026-01-01 to 2026-12-31), inside true; `attempt` {attempt_id 6, model flint, duration_ms 91,908, kept 2,
dropped 2}; `context` keys text, quote_start, quote_end, starts_mid_page, ends_mid_page; `ai_runs` lists runs 1 to 6
with status, prompt_version us03-v2 and 3 pages sent.

### Gap panel for the same gap (Evidence Review; the Prioritization row click shows the same six sections)

```
1 · Gap            COL-E · pt-e5d5... · no qualifying event in 2026 · deadline 31 Dec 2026 · last event none · pending review
2 · How likely     No practice record 0.50, evidence waiting +0.40, contacted 28 days ago +0.10 -> 0.98 (raw 1.00, kept 0.02 to 0.98)
3 · Stars          8 / 17 · 47.06% -> 47% · 1 star · next star 59% (10 done, 2 more) · weight 1 · 1 / 2 / 3 = 0.167
4 · Reachable      Contacted 28 days ago (within 90) 1.00
5 · Priority       composite = 0.98 x 0.167 = 0.1633 · medium · Abstractor review · reasons and action reason
6 · AI details     Run 10 · flint · succeeded · 1 kept, 0 dropped · prompt us03-v2 · pages sent U-6 p1, p2 · raw answer (folded)
```

On Prioritization, clicking the first row showed the panel card with the six headings and an "Open on Evidence
Review" button.

## Not done or known gaps

- `.env` still names the retired MiniMax free model, so AI review fails (cleanly, with the reason) on any app started
  from `.env` alone. Someone has to choose: a paid key for `minimax/minimax-m3.1-flash`, or a free model. Flint
  worked here only with `EXTRACTION_MAX_OUTPUT_TOKENS=32768`.
- The prompt is us03-v2 but was tested on Flint, not on the model the team tuned it for. Flint returned the frailty
  parts correctly once the schema listed `part`; quality on the other measures was not measured this round.
- The engine retry was not exercised: no engine crash happened in either batch.
- `GET /api/batches/latest` does not exist on the server; the page falls back to its last known batch.
- The confirmed findings wait in `closed_pending_engine` / `excluded_pending_engine` until the measure engine runs
  again on the exported records; that engine run was not part of this round.
