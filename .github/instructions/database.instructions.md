---
applyTo: "db/**,services/**/db.py,services/**/runner.py,services/**/review*.py,services/**/export*.py,scripts/db/**"
---

# Database: evidence lifecycle and the rules it enforces

The business rule (UC-HC-02): the AI finds and quotes evidence, a trained abstractor approves or rejects it, and
only approved evidence becomes supplemental data that the measure engine uses when it runs again. HEDIS auditors
can ask how any number was reached, so every step is traceable and nothing decided may change afterwards.

The schema follows the team schema (migration `0003_align_team_schema.py`). **The team tables are never changed**:
no added or renamed columns. What our features need beyond them lives in our own tables linked one to one by the team
IDs. Mapping and decisions: `docs/design/schema-alignment.md`.

```
model answer -> validator -> evidence_proposal (proposed) + evidence_proposal_detail (page, quote position)
                                   |
                       abstractor decision (review_decisions)
                       /                                \
              confirmed                               rejected (reason required)
                  |                                         |
     supplemental_records (approved evidence)      kept for audit, never re-proposed
                  |
     engine rerun with supplemental data -> gap closes
```

## IDs

- Patients: `patient.patient_id` is the FHIR ID; the member ID (`M-001`) is `patient.mrn`. Names are not in
  `patient`; they are in the FHIR Patient record in `fhir_resource_raw.raw`.
- Gaps: `care_gap.gap_id` is a uuid; seed gaps keep their code (`G-0001`) in `care_gap_detail.seed_code`.
- Documents: `document_reference.document_reference_id` (`D-101`, `U-52`); conversion details in
  `document_conversion`.

## What the database enforces

| Rule | How |
|---|---|
| New evidence starts as `proposed` | trigger `evidence_proposal_insert` |
| Status moves once, to `confirmed` or `rejected`, only with a matching `review_decisions` row; evidence fields never change | trigger `evidence_proposal_status` |
| Reviewer empty while proposed | CHECK on `evidence_proposal` (from the team diagram) |
| Evidence is never deleted; its detail never changes | `evidence_proposal_no_delete`, `evidence_proposal_detail_immutable` |
| The quote (`passage_citation`) matches the page text at the detail's offsets | trigger `evidence_proposal_detail_check` |
| The same quote is not proposed twice for a gap, even after rejection | unique key on `evidence_proposal_detail` |
| A rejection needs a reason | CHECK on `review_decisions` |
| Supplemental data only from confirmed evidence with a confirming decision | trigger `supplemental_records_confirmed_check` + foreign key to the decision |
| `measure_result` and `gap_priority_score` are append-only (read the `v_*_latest` views) | `*_append_only` triggers |
| Decisions, supplemental records, audit events and exports never change or disappear | `*_immutable` triggers |

Never disable, drop or bypass these, and never connect as a superuser to get past them. If a rule blocks something
legitimate, change it in a reviewed migration.

## Rules for code

- **Only a person decides.** Extraction inserts `proposed` evidence (`evidence_proposal` plus its
  `evidence_proposal_detail`, in one transaction) and nothing else in the review tables. Confirm and reject happen
  only in the review flow: insert the `review_decisions` row first, then update the proposal's status, reviewer and
  `decided_at`.
- **The reviewer comes from the session**, never from the request body or a default. Automated steps record their
  service name as the actor (`document_intake`, `evidence_extraction`).
- **One transaction per state change, audit included.** Status change, decision, supplemental record and audit
  events commit together or not at all.
- **Audit every change**: file received, converted, rejected; member linked; evidence proposed, confirmed or
  rejected; gap status changed; export created. Payloads carry IDs and codes, never names, dates or text.
- **Rejected evidence stays**: it is searchable for audit and blocks the same quote from coming back.
- **A gap closes only through the engine.** Our review states live in `care_gap_detail.review_status`;
  `care_gap.status` (`open`, `closed`) changes when the engine reruns with the supplemental data.
- **Lineage stays unbroken**: from any supplemental record you can reach the decision, reviewer, evidence,
  extraction attempt (model, prompt version), page, document, uploaded file and engine run. New tables in this
  flow carry the foreign keys that keep the chain.
- **Migrations**: new file `NNNN_snake_description.py` with a working downgrade; never edit an applied migration.
