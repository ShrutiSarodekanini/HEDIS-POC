# Schema alignment with the team schema

Migration `db/migrations/versions/0003_align_team_schema.py` replaces our 0001/0002 tables with the team schema
(Schema design.drawio, from the lead) **exactly as drawn**, plus our own tables linked to it. The team tables are
never changed: no added or renamed columns. Value lists and ranges written next to columns in the diagram are CHECK
constraints; "append-only" tables refuse updates and deletes; our review rules are triggers on the team tables
(agreed: triggers add protection, they do not change columns).

Pre-alpha: upgrading drops all data. Reload the seed (`python scripts/db/load_seed.py --reset`) and process the
documents again (`python -m services.document_intake.cli process-pending`).

## Decisions

| Topic | Decision |
|---|---|
| Who creates the team tables | Our migration, exactly as drawn |
| Table names | Lower snake case of the diagram names (`CARE_GAP` is `care_gap`) |
| MEASURE_DEFINITION | Key only (`measure_id`, `measurement_year`), because the diagram shows no other columns |
| Patient IDs | `patient.patient_id` = the FHIR/Synthea patient ID; `patient.mrn` = our member ID (`M-001`) |
| Patient names | Not in PATIENT. Read from the FHIR Patient record in `fhir_resource_raw.raw` (resource_type `Patient`) |
| Gender | `patient.gender` = FHIR code (`male`, `female`); `patient.birth_sex` = `M` or `F` |
| Gap IDs | `care_gap.gap_id` is a uuid. Seed gaps use uuid5 of their seed code; the code (`G-0001`) is kept in `care_gap_detail.seed_code` |
| Evidence status words | Team words: `proposed`, `confirmed`, `rejected` (we used PROPOSED, ACCEPTED, REJECTED) |
| Next visit, last contact | From ENCOUNTER: next visit = earliest `planned` encounter in the future; last contact = latest `finished` one |
| AAB visit | `care_gap_detail.episode_encounter_id` points at the encounter |
| batch_id | The seed loader writes one `source_batch`; rows created by document intake leave `batch_id` empty |

## Old table to new table

| Old (0001/0002) | New | Notes |
|---|---|---|
| `members` | `patient` + `fhir_resource_raw` (names) + `coverage` (product line as payer) | member_id is `patient.mrn` |
| `organizations` | `organization` + `organization_quality` (kind, state, closure_rate) | |
| `providers` | `practitioner` | `family_name` from the name; synthetic NPI |
| `member_engagement` | `encounter` | planned and finished encounters |
| `measures` | `measure_definition` (key) + `measure_rule` (everything else) | |
| `measure_star_info` | `measure_star_weight` (weights) + `star_cutpoint` (cut points, one row per star) | 2027 cut points filled in |
| `engine_runs` | `engine_runs` (ours, kept for lineage) | |
| `measure_summaries` | `measure_result` (one row per patient per measure) + `v_measure_result_latest` | rates computed from results |
| `gaps` | `care_gap` + `care_gap_detail` | priority columns moved to `gap_priority_score` |
| `documents` | `document_reference` + `document_conversion` | document ID unchanged (`D-101`, `U-52`) |
| `document_pages` | `document_pages` | `document_id` renamed `document_reference_id` |
| `source_files`, `structured_records` | same tables | `member_id` renamed `patient_id` |
| `evidence_candidates` | `evidence_proposal` + `evidence_proposal_detail` | `passage_citation` is the exact quote |
| `review_decisions` | same table | `candidate_id` is `evidence_id` (uuid); decision `confirmed` or `rejected` |
| `supplemental_records` | same table | points at `evidence_id`, `patient_id`, `measure_id` |
| `extraction_*`, `page_words`, `audit_events`, `export_*` | same tables | `gap_id` is a uuid |

## How evidence is written now

1. Insert `evidence_proposal` with `status = 'proposed'`, `reviewer_id` NULL, `passage_citation` = the exact quote,
   `proposed_finding` = a short summary, `extraction_model_version` = the model OpenRouter used.
2. In the same transaction insert `evidence_proposal_detail` (page, offsets, kind, type, dates, value, attempt,
   prompt version). A trigger checks the quote is on the page at those offsets.
3. To decide: insert `review_decisions` first, then update `evidence_proposal` to `confirmed` or `rejected` with the
   same reviewer and `decided_at`. A trigger refuses the status change without a matching decision, and refuses any
   change once decided.
4. Only for confirmed evidence: insert `supplemental_records`. A trigger checks both the decision and the status.

## Rules enforced by the database

| Rule | Where |
|---|---|
| New evidence starts as proposed | trigger `evidence_proposal_insert` |
| Status moves once, only with a matching decision; evidence fields never change | trigger `evidence_proposal_status` |
| Evidence is never deleted; its detail never changes | `evidence_proposal_no_delete`, `evidence_proposal_detail_immutable` |
| Quote matches the page text at its offsets | trigger `evidence_proposal_detail_check` |
| Reviewer empty while proposed | CHECK on `evidence_proposal` (from the diagram) |
| Reject needs a reason | CHECK on `review_decisions` |
| Supplemental data only from confirmed evidence | trigger `supplemental_records_confirmed_check` + foreign key to the decision |
| measure_result and gap_priority_score are append-only | `*_append_only` triggers |
| Decisions, supplemental records, audit events, exports never change | `*_immutable` triggers |

## Open points for the lead

- MEASURE_DEFINITION columns: when the lead's columns are known, our `measure_rule` keeps only what they lack.
- CARE_GAP has no link to the AAB visit; we keep it in `care_gap_detail.episode_encounter_id`.
- CARE_GAP status is `open` or `closed` only; our review states live in `care_gap_detail.review_status`.
- If a teammate later writes a migration for the same team tables, we agree who owns which.
