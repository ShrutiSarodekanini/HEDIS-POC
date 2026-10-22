"""OpenRouter client: sends one Prompt and returns the cleaned JSON answer, or raises ModelError.

OpenRouter is the only provider. The retries and clean-up here follow the quirks seen in the six-gap model
comparison (docs/design/us03-ai-evidence-extraction.md, "Known model behaviour").

Manual smoke test (one tiny synthetic prompt, no database): python -m services.evidence_extraction.model_client --smoke
"""

import argparse
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import date
from typing import Any

import httpx

from services.evidence_extraction.config import SETTINGS, Settings
from services.evidence_extraction.contracts import OUTPUT_SCHEMA, ModelError, ModelResult, PageRef, Prompt

log = logging.getLogger(__name__)

USER_AGENT = "hedis-poc/1.0 (+python)"
APP_TITLE = "HEDIS POC"
RATE_LIMIT_DEFAULT_WAIT_S = 20.0
RATE_LIMIT_MAX_WAIT_S = 60.0
RATE_LIMIT_MAX_WAITS = 3
BACKOFF_S = (2.0, 4.0, 8.0)
BODY_EXCERPT_CHARS = 300

# Failures where the same request may succeed a few seconds later.
_RETRYABLE_TRANSPORT_ERRORS = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


class OpenRouterClient:
    """ModelClient for OpenRouter's OpenAI-compatible chat completions endpoint."""

    def __init__(self, settings: Settings = SETTINGS, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        """Fail at construction without a key, so a run stops before any gap is touched."""
        if not settings.openrouter_api_key:
            raise ValueError("Set OPENROUTER_API_KEY in .env")
        self.model = settings.model
        self._settings = settings
        self._key = settings.openrouter_api_key
        self._transport = transport
        self._sleep = sleep
        self._headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "X-Title": APP_TITLE,
        }

    def complete(self, prompt: Prompt) -> ModelResult:
        """Send one prompt; empty, cut-off or failed answers raise ModelError so they never count as 'no evidence'."""
        body = self._request_body(prompt)
        started = time.perf_counter()
        with httpx.Client(transport=self._transport, timeout=self._settings.timeout_s) as client:
            response = self._post_with_retries(client, body)
        data = self._json(response)

        error = data.get("error")
        if error is not None:
            message = error.get("message", "") if isinstance(error, dict) else str(error)
            raise ModelError(f"OpenRouter returned an error instead of an answer: {message}", transient=True)
        choices = data.get("choices") or []
        if not choices:
            raise ModelError("OpenRouter returned no choices", transient=True)
        choice = choices[0]
        original = (choice.get("message") or {}).get("content") or ""
        if choice.get("finish_reason") == "length":
            raise ModelError(f"Answer was cut off at the {self._settings.max_output_tokens}-token output limit; "
                             "raise EXTRACTION_MAX_OUTPUT_TOKENS", transient=False, raw=original)
        text = clean_answer(original)
        if not text:
            raise ModelError("OpenRouter returned an empty answer", transient=True, raw=original)

        usage = data.get("usage") or {}
        return ModelResult(
            text=text,
            model=data.get("model") or self.model,
            duration_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )

    def _request_body(self, prompt: Prompt) -> dict[str, Any]:
        """temperature 0 and a fixed seed keep repeated runs comparable; json_schema asks for OUTPUT_SCHEMA."""
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": prompt.system}, {"role": "user", "content": prompt.user}],
            "temperature": 0,
            "seed": 42,
            "max_tokens": self._settings.max_output_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": "findings", "schema": OUTPUT_SCHEMA}},
        }

    def _post_with_retries(self, client: httpx.Client, body: dict[str, Any]) -> httpx.Response:
        """Retry what a later attempt can fix (429, 5xx, network); other 4xx fail at once, retrying cannot help."""
        rate_limit_waits = 0
        backoffs = 0
        while True:
            try:
                response = client.post(self._settings.openrouter_url, headers=self._headers, json=body)
            except _RETRYABLE_TRANSPORT_ERRORS as exc:
                failure = f"{type(exc).__name__}: {exc}"
            else:
                status = response.status_code
                if response.is_success:
                    return response
                if status == 400 and body["response_format"]["type"] == "json_schema":
                    log.warning("OpenRouter rejected response_format json_schema (HTTP 400); retrying with json_object")
                    body["response_format"] = {"type": "json_object"}
                    continue
                if status == 429:
                    if rate_limit_waits == RATE_LIMIT_MAX_WAITS:
                        raise ModelError(f"OpenRouter still rate limited (HTTP 429) after {rate_limit_waits} waits",
                                         transient=True)
                    rate_limit_waits += 1
                    wait = _retry_after_s(response)
                    log.warning("OpenRouter rate limited (HTTP 429); waiting %.0fs (wait %d of %d)",
                                wait, rate_limit_waits, RATE_LIMIT_MAX_WAITS)
                    self._sleep(wait)
                    continue
                if status < 500:
                    raise ModelError(f"OpenRouter returned HTTP {status}: {self._excerpt(response)}", transient=False)
                failure = f"HTTP {status}: {self._excerpt(response)}"

            if backoffs == len(BACKOFF_S):
                raise ModelError(f"OpenRouter failed after {backoffs} retries: {failure}", transient=True)
            wait = BACKOFF_S[backoffs]
            backoffs += 1
            log.warning("OpenRouter call failed (%s); retrying in %.0fs (retry %d of %d)",
                        failure, wait, backoffs, len(BACKOFF_S))
            self._sleep(wait)

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        """A 2xx body that is not a JSON object is a provider glitch, so it is transient rather than a crash."""
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise ModelError(f"OpenRouter returned a body that is not JSON: {self._excerpt(response)}",
                             transient=True) from exc
        if not isinstance(data, dict):
            raise ModelError(f"OpenRouter returned a body that is not a JSON object: {self._excerpt(response)}",
                             transient=True)
        return data

    def _excerpt(self, response: httpx.Response) -> str:
        """Start of a response body for error messages, with the key removed in case a body ever echoes it."""
        return response.text.replace(self._key, "[redacted]")[:BODY_EXCERPT_CHARS]


def _retry_after_s(response: httpx.Response) -> float:
    """Seconds to wait after a 429, capped so one rate-limited gap cannot stall a whole run."""
    try:
        wait = float(response.headers.get("retry-after", RATE_LIMIT_DEFAULT_WAIT_S))
    except ValueError:  # an HTTP-date retry-after; OpenRouter sends seconds
        wait = RATE_LIMIT_DEFAULT_WAIT_S
    return min(max(wait, 0.0), RATE_LIMIT_MAX_WAIT_S)


def clean_answer(text: str) -> str:
    """Remove <think> blocks and a surrounding ```json fence, which some models add around the JSON."""
    text = _THINK_BLOCK.sub("", text).strip()
    fenced = _JSON_FENCE.fullmatch(text)
    return (fenced.group(1) if fenced else text).strip()


def _smoke() -> int:
    """One tiny synthetic call to check the key, the model name and the network path by hand."""
    page = PageRef(document_id="D-SMOKE", page_no=1, doc_type="clinic note", document_date=date(2026, 9, 14),
                   source_org_id="ORG-TEST", method="plain_text", handwritten=False, low_confidence=False,
                   text="BP 132/78 on 2026-09-14")
    prompt = Prompt(
        system="Return JSON in the findings schema.",
        user=f"[DOC D-SMOKE | page 1 | clinic note | 2026-09-14 | ORG-TEST | plain_text]\n{page.text}",
        pages=(page,),
    )
    client = OpenRouterClient()
    print(f"asking {client.model} ...", flush=True)
    try:
        result = client.complete(prompt)
    except ModelError as exc:
        print(f"FAILED ({'transient' if exc.transient else 'permanent'}): {exc}")
        return 1
    print(f"model:   {result.model}")
    print(f"seconds: {result.duration_ms / 1000:.1f}")
    print(f"tokens:  {result.prompt_tokens} prompt, {result.output_tokens} output")
    print(f"answer:  {result.text}")  # phi-ok: synthetic smoke-test prompt only
    return 0


def main(argv: list[str] | None = None) -> int:
    """Command line entry point; only the manual smoke test lives here, runs start from the extraction CLI."""
    parser = argparse.ArgumentParser(prog="python -m services.evidence_extraction.model_client",
                                     description="OpenRouter model client for evidence extraction.")
    parser.add_argument("--smoke", action="store_true", help="send one tiny synthetic prompt and print the answer")
    args = parser.parse_args(argv)
    if not args.smoke:
        parser.error("nothing to do: pass --smoke")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return _smoke()


if __name__ == "__main__":
    raise SystemExit(main())
