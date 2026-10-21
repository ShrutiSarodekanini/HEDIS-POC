---
applyTo: "services/evidence_extraction/**,scripts/demo_extraction.py,scripts/demo_ui/**"
---

# AI model use (evidence extraction, US-03)

The POC uses one hosted provider, OpenRouter. `prompt.py` builds the prompt, `model_client.py` sends it,
`validator.py` checks the answer, and shared shapes live in `contracts.py`. New model calls go through
`OpenRouterClient`, never through a new HTTP call somewhere else.

## What may be sent

| Data | OpenRouter (hosted) | A model under a signed BAA, or local |
|---|---|---|
| Synthetic seed data (today's database) | Yes | Yes |
| Real patient documents | **No** | Yes, only the pages needed for the gap |
| Names, birth dates, member IDs in text we write | No | No |

A BAA (business associate agreement) is the contract HIPAA requires before a vendor handles real patient data.
Free tiers do not come with one. When the POC moves to real data, the provider is decided again, in writing,
before the first real document is sent.

## Prompts

- The header we write carries the gap, the measure rule and the member's **age and sex only**: no name, birth
  date, address, member ID or MRN. The `gap_id` ties the answer back.
- Page text is sent as it is, and clinical documents contain the patient's name, birth date and IDs. That is
  acceptable only under the table above. Planned: mask those identifiers in page text before sending, keeping
  quote positions exact.
- Send only the pages of the member the gap belongs to; never several members in one prompt.
- No PHI in request metadata (headers such as `X-Title`, a `user` field, provider tags).
- Change `PROMPT_VERSION` in `contracts.py` whenever the system prompt, the prompt layout or the output schema
  changes.

## The model proposes, people decide

- Every finding passes `validator.py` before it is saved: the quote is on the page word for word, the date is
  supported by the page and inside the measure window, the value meets the threshold.
- Findings that pass are saved as `proposed` evidence; only an abstractor confirms or rejects them.
- The model's `reason` is shown to reviewers as the model's explanation, not as fact.
- Validator rules come from the measure row (windows, thresholds, allowed types), never hard-coded per measure.

## Failures are not "no evidence"

Empty, cut-off or non-JSON answers and HTTP errors make the attempt `failed` with the reason, so it can be retried.
Only a well-formed answer with an empty findings list means "no evidence". A gap with no documents is "no
evidence" without calling the model.

## Repeatable calls

Temperature 0 and a fixed seed. Every attempt records the model OpenRouter actually used, the prompt version,
pages sent, tokens and time.

## Keys

- `OPENROUTER_API_KEY` in your own `.env`, one key per person.
- Never in code, tests, `.env.example`, docs, commits, issues, chats with any AI tool, or logs. Tests use fake keys
  (`sk-or-test-...`) and `httpx.MockTransport`, never the network.
- A key pasted anywhere it should not be is revoked in the OpenRouter dashboard and replaced.

## Quotas

The free tier allows about 50 requests a day (1,000 with credit bought); the 43 seed gaps need about 34 calls.
Debug with one gap, not the whole list. When the daily limit is hit, stop the run instead of retrying every gap.
