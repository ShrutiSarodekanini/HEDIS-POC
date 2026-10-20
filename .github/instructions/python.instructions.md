---
applyTo: "**/*.py"
---

# Python: coding standards and masking patient details

Write new code the way the existing services are written. When these rules and the code disagree, follow the code
that is wired into a running entry point, and raise the difference in review.

## Layout

```
services/<service>/   config.py (Settings + SETTINGS), db.py (connect, audit), api.py, cli.py, one module per job
db/migrations/versions/NNNN_snake_description.py   next number is the highest + 1
tests/<service>/test_<subject>.py                  helpers in _<service>_support.py
docs/decisions/NNNN-short-title.md                 one decision each
```

## Code

- Type hints on every function, parameter and return; `X | None`, not `Optional[X]`.
- Docstrings and comments say **why**, not what. One short docstring per public function.
- Line length 120; `ruff check` must pass on files you touched. Do not reformat files you did not change.
- Functions about 40 lines, modules about 200 to 300 lines; split when they grow.
- Frozen dataclasses for settings and value objects. Shapes shared between modules live in one `contracts.py` per
  service; change them only together with every module that uses them.
- `log = logging.getLogger(__name__)`; no `print` outside CLI output. Delete dead code instead of commenting it out.
- Add a dependency only when the standard library or an existing one cannot do the job, with a one-line reason in
  `requirements.txt` (`pillow-heif       # iPhone HEIC photos`).

## Settings

Everything that changes between machines (URLs, ports, paths, keys, model names) comes from environment variables
read by the service's `config.py` into a frozen `Settings`. `.env.example` lists every variable with a placeholder,
never a real value. In tests, pass `dataclasses.replace(SETTINGS, ...)` instead of patching the environment.

## Database code

- SQL lives in the service module that owns the data, always with parameters:
  `conn.execute("... WHERE id = %s", (file_id,))`. Never build SQL from values with f-strings or `+`.
- The caller owns the transaction: library functions do not commit; the API request or CLI command does.
- Schema changes are a new Alembic migration with a working downgrade. Never edit a migration someone may have
  applied.

## Errors

- Raise specific exceptions (`LookupError`, `ValueError`, or a small named class) with a message that says what
  went wrong and what to do; HTTP routes turn them into `HTTPException` with a deliberate status code.
- No bare `except:`, no `except Exception: pass`, no log-and-continue that hides a failure. When one item in a batch
  fails, record the failure on that item with its reason and carry on with the others.

## Patient details (PHI)

Treat these as PHI, even in synthetic data: names; birth dates and any date tied to a person except the year;
address, ZIP beyond 3 digits, phone, fax, email; member ID (`M-001`), MRN, Synthea patient UUID, plan or account
numbers; upload file names; document and page text, OCR output, quotes (excerpts), prompts and model answers;
page images. Age in whole years and sex are fine. Internal keys (`source_file_id`, `document_id` such as `D-601`,
`gap_id`, `attempt_id`) are fine to log: they mean nothing outside our database.

PHI belongs in the database tables and the encrypted file store built to hold it, and on screens for the people
allowed to see it. Nowhere else: not in logs, error messages, exception text, metrics, audit payload values, file
names we create, commit messages, test names, or prompts we write ourselves.

```python
# Wrong: a file name can be "John_Smith_mammogram.pdf"; the text is the chart itself
log.warning("virus scan of %s could not run: %s", filename, exc)
log.info("matched %s %s born %s", first_name, last_name, birth_date)
raise ValueError(f"no member found for {name}, born {dob}")

# Right
log.warning("virus scan of source file %s could not run: %s", source_file_id, type(exc).__name__)
log.info("source file %s matched to a member by name and birth date", source_file_id)
raise LookupError("no member matches this file; it is waiting in the member-match queue")
```

- **Library exception text can carry PHI** (a parser quoting the line it failed on). Log `type(exc).__name__` and
  your own message.
- **Never log** request bodies, uploaded bytes, page text, prompts or model answers. Raw model answers go to
  `extraction_attempts.raw_response` (protected storage), not to a log. Avoid `exc_info=True` where locals hold text.
- **HTTP error details** carry IDs and plain reasons only. Echoing back an ID the caller sent is fine.
- **Files we create** are named by ID or hash, never by patient name; refer to an upload by `source_file_id`.
- **Audit payloads** reference records by ID (`member_id`, `document_id` are fine: the audit table sits in the same
  protected database) and describe the action with codes. No names, birth dates, excerpts, page text or file names.

```python
audit(conn, "evidence", str(candidate_id), "evidence.rejected", {"reason_code": "outside_window", "gap_id": gap_id})
```

When text output for people must show a value, mask it: name as initials (`R. H.`), member ID or MRN as the last 3
characters (`M-***001`), birth date as the year, other personal dates as month and year, document text as length
and location (`[excerpt, 84 chars, D-601 page 1]`). Use one small helper module for this rather than masking by
hand in each place.

## Tests

- `tests/<service>/test_<subject>.py`; test behaviour through public functions, not private helpers.
- Database tests run inside a transaction that is always rolled back.
- Test data is synthetic. Never copy a real record into a test, fixture, seed file, issue or chat, even to
  reproduce a bug: make up a record with the same shape.
- A new rule gets a passing and a failing test; a bug fix gets a test that failed before the fix. Never weaken,
  skip or delete a test to make it pass.
