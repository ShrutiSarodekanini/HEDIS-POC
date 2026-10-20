---
applyTo: "**"
---

# Git and reviews

## Branches

- Branch from the branch your story builds on: `feat/usNN-short-slug` (for example
  `feat/us03-ai-evidence-extraction`) or `fix/short-slug`.
- Never commit to `main` directly; every change reaches `main` through a reviewed pull request.
- One story per branch. If it builds on a story that is not merged yet, branch from it and say so in the PR.

## Commits

Conventional Commits: `type(scope): imperative summary`, about 72 characters at most. Types: `feat`, `fix`,
`refactor`, `test`, `docs`, `chore`. The body says why and what a reviewer must know.

- Commits read as written by the team: no AI tool attribution lines or "generated with" banners.
- No patient details in commit messages, branch names or PR text, even synthetic ones; refer to records by
  internal ID (`G-0040`, `D-601`).

## Never commit

| What | Why |
|---|---|
| `.env`, any file with a real key or password | Secrets; `.env.example` holds placeholders only |
| `data/intake_store/` | Encrypted originals and page images of uploaded documents |
| Real patient data of any kind | Only synthetic data belongs in the repo |
| Database dumps, exports, logs from a run | They can contain PHI |
| Large binaries that are not test fixtures | They bloat the repo for everyone |

Before committing: `git diff --cached --stat`, then
`python .claude/skills/hedis-team-standards/scripts/phi_check.py --staged`. If a secret or patient data was
committed, tell the team at once: a later delete does not remove it from history; rotate the key and clean the
history.

## Pull requests

Say what changed, why, how it was tested (command and result), and what was left out on purpose. Link the story
(US-NN) and task.

## Review checklist

- [ ] Tests for the change exist and pass; the output is in the PR.
- [ ] `ruff check` passes on the changed files.
- [ ] No names, birth dates, member IDs, file names or document text in logs, errors, audit payloads or prompts we
      write (`phi_check.py` run, findings explained).
- [ ] No secrets in the diff; `.env.example` has placeholders only.
- [ ] SQL uses parameters.
- [ ] Failures are recorded with a reason; nothing swallowed; no error turned into "no evidence".
- [ ] State changes write an audit event in the same transaction, with the real actor.
- [ ] No path lets a model or extraction accept evidence, create supplemental data or close a gap.
- [ ] Database protections are not bypassed.
- [ ] New dependencies have a one-line reason; schema changes are a new migration with a downgrade.

Write review comments as questions to a colleague, with the reason: "Could this log the source file ID instead of
the file name? Upload names can contain the patient's name."
