# Tracking report: every step of the flow written to the schema's own tables

Branch `ui-integration`, schema `ui20`, screens app on port 8785. Nothing is committed or pushed. All data is
synthetic (Synthea). No table or column was added, no structure of the lead's tables changed, and no file of
the ingestion or star rating code was edited.

## What is now tracked

| Step | Tables written | Where |
|---|---|---|
| Review with AI | `extraction_runs` (one per click), `extraction_attempts` (one per model call), `evidence_proposal_detail.attempt_id` (each saved finding points to its attempt), audit `extraction.completed` or `extraction.failed` | `services/integration/evidence.py` (`find_evidence`, `_attempt`, `_finish_run`) |
| Confirm a finding | `supplemental_records` (with the confirming `review_decisions.id`), audit `supplemental.recorded` | `evidence.py` (`decide`, `_record_supplemental`) |
| Every decision | `star_scenario` (one snapshot of the gap's measure) | `evidence.py` (`_star_scenario`) |
| Export approved evidence | `export_runs`, `export_run_items`, a CSV in `<INTEGRATION_WORK_DIR>/exports`, audit `supplemental.exported` | `services/integration/export.py`, `POST /api/supplemental/export` |
| Load | `medication_request`, `immunization`, `feed_completeness_report` (as well as the earlier tables) | `services/data_ingestion/team_schema_loader.py` |

Details:

- **Runs and attempts.** The run row is inserted (status running, provider openrouter, the configured model,
  scope with gap, measure, trigger `review_with_ai` and page count) and committed before any call. Each call's
  attempt row is inserted and committed when the call ends, before any of its findings: succeeded (at least one
  finding kept), no_evidence (answer parsed, nothing kept) or failed (ModelError or UnusableAnswer, with the message
  as `error`). `raw_response` is the model's answer text; for a ModelError it is the error's raw text when it has
  one, and for an unparseable answer it is that answer. `duration_ms` and the token counts come from the model
  result; a call that failed before any result gets the measured time and no tokens. The run then gets
  `finished_at` and completed, or failed when every call failed. An unexpected error also closes the run as failed
  before it is raised. With no pages the run is completed with no attempts and the response note is unchanged.
- **Supplemental records.** Written in the decision's transaction, after the proposal is set to confirmed, from
  `evidence_proposal_detail` and the gap's `care_gap` row. The database trigger accepts it only with a confirming
  decision for the same evidence. Rejects write none.
- **Star scenario.** After the decision and re-score, from the same numbers the measure cards show
  (`work.measures`, which uses `assign_star_from_cutpoints` from the star rating code): baseline and low = the current rate and
  stars (the engine accepts none of the confirmed evidence), high = the projected rate and stars,
  `gaps_closed_assumed` = confirmed closures waiting for the engine. Skipped with a log line when a star rating is
  not available.
- **Export.** Takes every supplemental record no export contains, under an advisory lock so two exports cannot pick
  the same records, writes the CSV, computes its sha256, then records the run and its items in one transaction (the
  file is removed if that fails). Nothing new returns `{"rows": 0}` and creates no run. The actor is the reviewer ID
  in the body, checked like a decision's reviewer, or the configured reviewer.
- **Loader.** Same filter as the other inserts (rows the persist stage approved), `ON CONFLICT DO NOTHING`, so it
  stays safe to run twice. Immunization points to its patient with `patient` (not `subject`) and the mapping gives
  it no encounter, so `immunization.encounter_id` is empty. The feed report has one row per resource type, with
  `measure_relevant` true for Patient, Encounter, Condition, Observation, Procedure and Coverage, and is skipped when
  the batch already has rows. The new counts appear in `batch_run.counts.load.inserted`.
- **Read side.** `GET /api/gaps/{gap_id}` adds `ai_runs` (per attempt, newest first: run time, model, status, kept,
  dropped with reasons, tokens, duration, first line of the error). `GET /api/measures` adds to its evidence block
  `supplemental_total`, `supplemental_unexported` and the last 5 exports (id, time, rows, first 12 characters of the
  sha256).
- **Screens (Evidence Review).** Under "Run AI evidence extraction", the chosen gap's AI history, with the dropped
  findings and their reasons in an expandable list. A new card "Export approved evidence" with the count not yet
  exported, the result of the export (rows, file name, sha256 prefix) and the last exports.

## Run on `ui20`

The screens app on 8785 was stopped and started again with the new code (detached, as the brief's command), then:
Start over on `ui20` (13 s), upload `D:\HEDIS_Synthea_20\synthea_20_patients.zip`, finished in 15 minutes (30
documents attached). Review with AI on two COL-E gaps through the API: `c72faab2` with a closure document (SD-001)
and `f48e7755` with an exclusion document (SD-005). Confirmed the closure finding and rejected one of the two
exclusion findings (reviewer `abstractor.check`), then exported. The apps on 8780 and 8783 were not touched.

### Every table in `ui20`

| Table | Rows | Why empty |
|---|---|---|
| alembic_version | 1 | |
| allergy_intolerance | 0 | The ingestion mapping produces no AllergyIntolerance resources. |
| audit_events | 168 | |
| batch_run | 1 | |
| care_gap | 34 | |
| care_gap_detail | 34 | |
| condition | 947 | |
| conversion_jobs | 1 | |
| coverage | 1,173 | |
| data_anomaly | 0 | Data quality checks are on branch `feat/data_quality_anomoly_detection`. |
| document_conversion | 30 | |
| document_pages | 33 | |
| document_reference | 30 | |
| domain_event | 8 | |
| encounter | 1,645 | |
| engine_runs | 1 | |
| evidence_proposal | 3 | |
| evidence_proposal_detail | 3 | |
| export_run_items | 1 | |
| export_runs | 1 | |
| extraction_attempts | 2 | |
| extraction_runs | 2 | |
| feed_completeness_report | 10 | |
| fhir_resource_raw | 34,524 | |
| fhir_resources | 34,524 | |
| gap_priority_reason | 170 | |
| gap_priority_score | 170 | |
| immunization | 271 | |
| location | 0 | The ingestion mapping produces no Location resources. |
| measure_definition | 10 | |
| measure_result | 120 | |
| measure_rule | 10 | |
| measure_star_weight | 6 | |
| medication_request | 1,162 | |
| observation | 22,092 | |
| organization | 16 | |
| organization_quality | 16 | |
| page_words | 1,146 | |
| patient | 20 | |
| practitioner | 11 | |
| procedure | 4,329 | |
| reconciliation_result | 0 | Needs a second, incumbent engine to compare with. |
| review_decisions | 2 | |
| source_batch | 2 | |
| source_files | 30 | |
| star_cutpoint | 30 | |
| star_scenario | 2 | |
| structured_records | 0 | Filled only when a structured lab file goes through document intake; this run had none. |
| supplemental_records | 1 | |
| validation_issues | 0 | Ingestion's; filled when its validation finds errors, and this batch had none (0 errors). |

The six empty tables are exactly the ones section 6 of the brief leaves empty on purpose.

### One `extraction_attempts` row (without `raw_response`)

```json
{"id": 1, "run_id": 1, "gap_id": "c72faab2-7262-5413-af76-7d75d92d0400", "status": "succeeded", "error": null,
 "prompt_version": "us03-v1",
 "pages_sent": [{"method": "text_layer", "page_no": 1, "document_id": "U-1", "handwritten": false}],
 "dropped": [], "duration_ms": 5033, "prompt_tokens": 1081, "output_tokens": 413, "raw_response_chars": 370,
 "claimed_at": "2026-10-22 16:58:16+05:30", "created_at": "2026-10-22 16:58:21+05:30"}
```

Both runs: provider openrouter, model `minimax/minimax-m3.1-flash:free`, status completed, scope
`{"gap_id", "measure": "COL-E", "trigger": "review_with_ai", "pages": 1}`. Attempt 2 (`f48e7755`): succeeded, 2
kept, 1,079 + 2,674 tokens, 31.3 s. Finding links: attempt 1 has 1 detail row, attempt 2 has 2. Audit:
`extraction.completed` x2 (actor `integration`).

### The `supplemental_records` row

```json
{"id": 1, "evidence_id": "7cd8c223-150c-4560-b70b-f8d281a91c13", "decision_id": 1,
 "patient_id": "6afc7ef4-b65d-5584-9b51-0f2f71223690", "measure_id": "COL-E", "measurement_year": 2026,
 "evidence_kind": "numerator", "evidence_type": "colonoscopy", "service_date_from": "2021-06-12",
 "service_date_to": "2021-06-12", "value": "cecum reached, no polyps", "document_reference_id": "U-1", "page_no": 1}
```

Audit `supplemental.recorded` (actor `abstractor.check`). The rejected finding wrote no supplemental record.

### The export

```json
{"export_run_id": 1, "rows": 1, "actor": "abstractor.check", "filter": {"unexported_only": true},
 "file_path": ".integration_work/exports/supplemental-20261022T112914Z.csv",
 "file_sha256": "4df46fa12aaee9fada8b242655d4282d89a7cbcd12059e6cfdc44470c2ad615b"}
```

The sha256 of the file on disk matches the stored one. `export_run_items`: (1, 1). A second export right after
returned `{"rows": 0}` and created no run. First two lines of the file:

```
supplemental_record_id,patient_id,measure_id,measurement_year,evidence_kind,evidence_type,service_date_from,service_date_to,value,document_reference_id,page_no,evidence_id,decision_id
1,6afc7ef4-b65d-5584-9b51-0f2f71223690,COL-E,2026,numerator,colonoscopy,2021-06-12,2021-06-12,"cecum reached, no polyps",U-1,1,7cd8c223-150c-4560-b70b-f8d281a91c13,1
```

### `star_scenario`

| After | Measure | Baseline | Closures assumed | Low rate / stars | High rate / stars | By |
|---|---|---|---|---|---|---|
| Confirm (`c72faab2`) | COL-E 2026 | 47.1% | 1 | 47.1% / 1 | 52.9% / 1 | abstractor.check |
| Reject (`f48e7755`) | COL-E 2026 | 47.1% | 1 | 47.1% / 1 | 52.9% / 1 | abstractor.check |

COL-E is 8 of 17 met (47.1%). With the confirmed closure the high case is 9 of 17 (52.9%), still 1 star. The reject
changes nothing, so its snapshot repeats the numbers. The exclusion gap went back to `pending_review`, not `open`,
because SD-005 produced two near-identical exclusion findings and only one was rejected; the other waits in the
review queue.

### Load counts on `batch_run`

`patient` 20, `coverage` 1,173, `encounter` 1,645, `condition` 947, `observation` 22,092, `procedure` 4,329,
`medication_request` 1,162 (all with an encounter), `immunization` 271 (no encounter, see above),
`feed_completeness_report` 10, `fhir_resource_raw` 34,524.

## Files changed in this round

| File | Change |
|---|---|
| `services/integration/evidence.py` | Run and attempt tracking, `attempt_id` on saved findings, supplemental record on confirm, star scenario after each decision, `clean_reviewer` shared with the export. |
| `services/integration/export.py` | New. `export_supplemental`. |
| `services/integration/app.py` | `POST /api/supplemental/export`, the one new endpoint. |
| `services/integration/work.py` | `ai_runs` on a gap; supplemental and export totals in the evidence block of `/api/measures`. |
| `services/data_ingestion/team_schema_loader.py` | `medication_request`, `immunization`, `feed_completeness_report`. |
| `services/integration/ui/redesign/index.html`, `app.js`, `styles.css` | AI history under "Run AI evidence extraction", the "Export approved evidence" card. |
| `docs/integration/TRACKING_REPORT.md` | This report. |

The uncommitted work already on the branch (Dashboard, Run Ingestion, Run Gap Calculation screens, Start over,
`-UiDir`, `-AllowReset`) is kept as it was.

Checks: `ruff check services/integration services/data_ingestion/team_schema_loader.py` passes; `node --check` on
`app.js` passes; `phi_check.py` on the changed files and this report: no findings.

## Could not do, or to note

- **`GET /api/batches/latest` does not exist on the server.** The brief lists it as uncommitted work, and
  `app.js` calls it when no batch id is stored, but no route defines it, so the call falls through to
  `GET /api/batches/{batch_id}` with the id "latest", gets a 404 and the page treats it as "no batch". I did not add
  it, since the export is to be the one new endpoint of this round; to decide.
- The dropped-findings list on the AI history could not be seen on the screen in this run, because neither model
  call dropped a finding. The data path is in place (`dropped` and `dropped_count` per attempt).
- No call failed in this run, so a `failed` attempt row was not produced here; the code path writes one with the
  error and raw text, and closes the run as failed when every call fails.
- The screens app on 8785 was started detached (Start-Process) with the brief's parameters so that it keeps running
  after the terminal closes; logs in `%TEMP%\app_8785.log` and `.err`.
