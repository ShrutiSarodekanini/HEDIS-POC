# HEDIS POC: team rules

This repo handles health records for HEDIS quality measures. All data today is synthetic, but write every line as
if it were real: a name in a log file or a prompt sent to the wrong model is a reportable privacy breach (HIPAA)
and cannot be undone.

More detailed rules load automatically from `.github/instructions/` for the files you work on:

| File | Applies to | Covers |
|---|---|---|
| `python.instructions.md` | all Python | coding standards, and masking patient details in logs, errors and audit records |
| `ai-model-use.instructions.md` | `services/evidence_extraction/**` | prompts, OpenRouter, keys, failures, quotas |
| `database.instructions.md` | `db/**`, database and review code | evidence lifecycle and the rules the database enforces |
| `git-and-reviews.instructions.md` | everything | branches, commits, what never to commit, review checklist |

## The seven rules that matter most

1. **No patient details in logs, error messages, audit payload values or prompts we write.** Patient details are
   names, birth dates and other dates tied to a person (except the year), addresses, phone, email, member IDs,
   MRNs, upload file names, and any document or page text. Log internal record IDs instead
   (`source_file_id=52`, `document_id=D-601`, `gap_id=...`).
2. **The model only proposes.** AI output is validated, saved as `proposed` evidence, and decided by a person. No code
   written by a model or by extraction may approve evidence, create supplemental data or close a gap.
3. **Synthetic data only to hosted models such as OpenRouter.** Real patient documents only go to a model under a
   signed business associate agreement (BAA) or one that runs locally.
4. **Secrets live in `.env` only.** Never in code, tests, `.env.example`, docs, commits, chats or logs. A key pasted
   anywhere else is leaked and must be rotated.
5. **Never bypass the database rules.** Decisions, supplemental records and audit events cannot be changed or
   deleted, and only accepted evidence becomes supplemental data. Do not disable triggers or use a superuser.
6. **A failure is a failure.** Record it with a reason. Never swallow an exception, and never turn an error or an
   unreadable answer into "no evidence" or an empty result.
7. **Every state change writes an audit event** in the same transaction, with the real actor.

## Stack and layout

Python 3.11+, FastAPI, psycopg 3, PostgreSQL, Alembic, pytest, ruff (line length 120). One folder per service
under `services/`; settings from the repo-root `.env` through each service's `config.py`; SQL only with
parameters. Do not create new top-level folders without agreeing it with the team.

## Before you finish a change

```
python -m pytest tests/<area> -q
ruff check <files you changed>
python .claude/skills/hedis-team-standards/scripts/phi_check.py --staged
```

`phi_check.py` flags likely patient details in log, print, error and audit calls, API keys, and files that must
never be committed. A line you keep on purpose gets `# phi-ok: <reason>`.
