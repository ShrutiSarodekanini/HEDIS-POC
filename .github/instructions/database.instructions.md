---
applyTo: "db/**,services/**/db.py,services/**/runner.py,services/**/review*.py,services/**/export*.py,scripts/db/**"
---

# Database: evidence lifecycle and the rules it enforces

The business rule (UC-HC-02): the AI finds and quotes evidence, a trained abstractor approves or rejects it, and
only approved evidence becomes supplemental data that the measure engine uses when it runs again. HEDIS auditors
can ask how any number was reached, so every step is traceable and nothing decided may change afterwards.

Table names are today's schema (`db/migrations/versions/0001_initial_schema.py`). If the team schema is adopted,
the names change; the rules do not.

```
model answer -> validator -> evidence_candidates (PROPOSED)
                                   |
                       abstractor decision (review_decisions)
                       /                                \
              ACCEPTED                                REJECTED (reason required)
                  |                                         |
     supplemental_records (approved evidence)      kept for audit, never re-proposed
                  |
     engine rerun with supplemental data -> gap closes
```

## What the database enforces

| Rule | How |
|---|---|
| A candidate's status moves once, from PROPOSED to ACCEPTED or REJECTED | trigger `evidence_candidates_guard` |
| The quote must match the page text at its offsets | trigger `evidence_candidates_excerpt_check` |
| Candidates are never deleted | trigger `evidence_candidates_no_delete` |
| A rejection needs a reason | check on `review_decisions` |
| Decisions, supplemental records, audit events and exports never change or disappear | `*_immutable` triggers |
| Supplemental data only from an ACCEPTED candidate with an ACCEPTED decision | composite foreign key and trigger `supplemental_records_accept_check` |
| The same quote is not proposed twice for a gap, even after rejection | unique key on gap, document, page, offsets |

Never disable, drop or bypass these, and never connect as a superuser to get past them. If a rule blocks something
legitimate, change it in a reviewed migration.

## Rules for code

- **Only a person decides.** Extraction inserts `PROPOSED` candidates and nothing else in the review tables (insert
  with `ON CONFLICT DO NOTHING`). Accept and reject happen only in the review flow, by an abstractor.
- **The reviewer comes from the session**, never from the request body or a default. Automated steps record their
  service name as the actor (`document_intake`, `evidence_extraction`).
- **One transaction per state change, audit included.** Status change, decision, supplemental record and audit
  events commit together or not at all.
- **Audit every change**: file received, converted, rejected; member linked; candidate proposed; evidence approved
  or rejected; gap status changed; export created. Payloads carry IDs and codes, never names, dates or text.
- **Rejected evidence stays**: it is searchable for audit and blocks the same quote from coming back.
- **A gap closes only through the engine.** "Closed" on our screens means approved evidence exists; the official
  result changes when the engine reruns with the supplemental data.
- **Lineage stays unbroken**: from any supplemental record you can reach the decision, reviewer, candidate,
  extraction attempt (model, prompt version), page, document, uploaded file and engine run. New tables in this
  flow carry the foreign keys that keep the chain.
- **Migrations**: new file `NNNN_snake_description.py` with a working downgrade; never edit an applied migration.
