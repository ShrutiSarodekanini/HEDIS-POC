# US-03 AI evidence extraction: design

For each open gap, send the member's converted pages and the measure rule to a model, check every finding the
model returns, and save the ones that pass as `PROPOSED` evidence candidates. An abstractor approves or rejects
them in US-04. The model never closes a gap, and nothing in this package creates review decisions or
supplemental records (the database refuses that anyway).

## Package

`services/evidence_extraction/`

| Module | Owns | Built in |
|---|---|---|
| `contracts.py` | The shapes passed between modules, `OUTPUT_SCHEMA`, `PROMPT_VERSION` | done |
| `config.py` | Settings from `.env` (model, OpenRouter key, limits) | done |
| `model_client.py` | OpenRouter calls | Brief A |
| `prompt.py` | Loading a gap and its measure, picking pages, building prompts | Brief B |
| `validator.py` | Parsing the answer and checking every finding | Brief C |
| `runner.py`, `cli.py` | Runs, attempts, saving candidates, gap counts, audit | Brief D |
| `scripts/eval_extraction.py` | Scoring a run against `expected_findings.json`, model comparison | Brief E |

A, B and C only meet through `contracts.py`, so they can be built at the same time. D needs A, B and C. E needs D.

## Flow for one gap

1. `prompt.load_gap_context` reads the gap, the member's age on 31 December and sex. No name, birth date or
   address leaves the database.
2. `prompt.load_measure` reads the measure row (windows, thresholds, allowed evidence types, prompt template).
3. `prompt.select_pages` picks the member's converted pages, newest first, skipping near-duplicates.
4. `prompt.build_prompts` fills the template, labels every page `[DOC D-101 | page 1 | lab report | 2026-05-10 |
   ORG-RIVER | text_layer]`, and splits the pages into several prompts when they exceed the token budget. Pages
   are never dropped to fit.
5. `model_client.complete` sends each prompt. Empty, cut-off or non-JSON answers are failures, not "no evidence".
6. `validator.parse_findings` turns the answer into `Finding`s; `validator.validate` keeps or drops each one with
   a reason.
7. `runner` saves kept findings as `PROPOSED` candidates, records the attempt (prompt version, pages sent, drops,
   tokens, time, raw answer), updates the gap's pending count and writes audit events. One transaction per gap.

## Validator rules come from the measure row, not from code

| Check | Source |
|---|---|
| Evidence type is allowed for this kind | `measures.allowed_evidence_types` |
| Service date is inside the type's window | `measures.windows` (`from` null means any time up to `to`) |
| Newer than the last known event | `measures.most_recent_counts` and `gaps.last_event.date` |
| BP under 140/90, HbA1c under 8.0 % | `measures.thresholds` |
| uACR in mg/g; albumin and creatinine within 4 days | `measures.thresholds` (KED) |
| Age-limited exclusions (`..._age_66_plus`) | `GapContext.age` |

SPD and AAB have no numerator types, so any numerator finding for them is dropped by the first check.

## Decisions

- **No HTTP endpoints yet.** Runs start from the CLI. The intake POST endpoints were set aside for now, and the
  extraction API (`POST /api/extraction-runs`) follows the same rule. Add it when the UI needs it.
- **Gap status.** Extraction only sets `pending_count` and moves `open` to `pending_review` when a candidate was
  saved. The full status rules (partially evidenced, closed, excluded) are US-05 T1.
- **Re-runs.** `UNIQUE (gap_id, document_id, page_no, char_start, char_end)` means a finding already proposed or
  already rejected is not proposed again. Insert with `ON CONFLICT DO NOTHING`.
- **One provider: OpenRouter.** Default model Nemotron 3 Super (free), the best result in the comparison.
  Ollama and Groq are not supported. The model name is a setting, so another OpenRouter model needs no
  code change. The key lives in `.env` only, and only synthetic data may be sent.
- **Prototype.** `docs/design/us03-prototype/hedis_demo_run.py` is the script from the six-gap model comparison.
  Reuse its system prompt wording and provider quirks; do not copy its structure or its demo data.

## Known model behaviour (from the six-gap comparison)

Nemotron 3 Super on OpenRouter found the right evidence for 6 of 6 gaps (qwen3:8b local 5 of 6, gpt-oss-120b on
Groq 4 of 6). Free tier: 50 requests a day, 1,000 a day once $10 of credit has been bought.

OpenRouter quirks seen in the prototype: reasoning models need an 8,192-token output limit or the JSON is cut
off; some models reject `json_schema` with HTTP 400, so fall back to `json_object`; 429 comes with a
`retry-after` header; the answer can arrive wrapped in a ```json fence or after a `<think>` block.
