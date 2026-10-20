---
name: hedis-team-standards
description: Team rules for the HEDIS POC repo covering Python coding standards, masking patient details (PHI) in logs, errors, audit payloads and AI prompts, safe use of AI models (OpenRouter), git and review practice, and the audit and human-in-the-loop rules the database enforces. Use this skill for any code change in this repo, before writing or reviewing code that touches members, documents, gaps, evidence or model calls, whenever someone logs, prints or raises an error that could include patient data, builds a prompt, adds a dependency, commits, opens a pull request or reviews one, even if the request does not mention standards, privacy or PHI.
---

# HEDIS POC team standards

The rules live in `.github/`, where GitHub Copilot applies them automatically, so Claude Code and Copilot follow
one copy. Read them from there:

1. Always read `.github/copilot-instructions.md`: the seven rules that matter most, the stack, and what to run
   before finishing a change.
2. Then read the file in `.github/instructions/` for the area you are working in:

| Working on | Read |
|---|---|
| Any Python code: layout, types, SQL, errors, settings, logging, dependencies, and masking patient details | `.github/instructions/python.instructions.md` |
| Prompts, model calls, OpenRouter, API keys (`services/evidence_extraction/`) | `.github/instructions/ai-model-use.instructions.md` |
| Migrations, evidence, review decisions, supplemental data, audit events | `.github/instructions/database.instructions.md` |
| Branches, commits, pull requests, reviews | `.github/instructions/git-and-reviews.instructions.md` |

Each file starts with an `applyTo` pattern; that is the set of files it is written for.

## Checker

Before finishing a change, run:

```bash
python .claude/skills/hedis-team-standards/scripts/phi_check.py --staged
```

It scans what is about to be committed for likely patient details in log, print, error and audit calls, for API
keys, and for files that must never be committed (`.env`, `data/intake_store/`). It reports suspects; you decide. A
finding kept on purpose gets `# phi-ok: <reason>` on that line. `phi_check.py PATH` scans whole files or folders.

## When a rule seems to get in the way

Ask before bending it. If a task really needs patient details somewhere the rules forbid (for example a reviewer
screen that must show the member's name), the answer is usually "show it in the authorised screen, keep it out of
logs and prompts", not an exception to the rule. Write the reason in the pull request.
