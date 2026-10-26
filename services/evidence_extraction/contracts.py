"""The shapes passed between the extraction modules. Each module owns its logic; this file owns the hand-offs.

    prompt.py      GapContext + measure row + PageRefs  ->  list[Prompt]
    model_client   Prompt                               ->  ModelResult        (raises ModelError)
    validator.py   ModelResult.text                     ->  list[Finding]      (raises UnusableAnswer)
                   GapContext + measure + PageRefs + Findings  ->  ValidationResult
    runner.py      ties them together and writes extraction_attempts and evidence_candidates

Change a shape here only together with every module that uses it.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

#: Bump when the system prompt, the prompt layout or OUTPUT_SCHEMA changes. Stored on every attempt and candidate.
PROMPT_VERSION = "us03-v2"

#: JSON schema the model must answer with, sent to OpenRouter as response_format json_schema.
#: page_no is the page label the model saw: [DOC D-101 | page 1 | ...].
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "page_no": {"type": "integer"},
                    "excerpt": {"type": "string"},
                    "evidence_kind": {"type": "string", "enum": ["numerator", "exclusion"]},
                    "evidence_type": {"type": "string"},
                    "service_date": {"type": "string"},
                    "value": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                    "part": {"type": "string", "enum": ["", "frailty", "advanced_illness"]},
                },
                "required": ["document_id", "page_no", "excerpt", "evidence_kind", "evidence_type",
                             "service_date", "value", "confidence", "reason", "part"],
            },
        }
    },
    "required": ["findings"],
}


@dataclass(frozen=True)
class GapContext:
    """One gap with what the prompt and the validator need. No name, birth date or address: age and sex only."""
    gap_id: str                   # the care_gap uuid as a string
    patient_id: str
    measure_code: str
    measurement_year: int
    age: int                      # on 31 December of the measurement year
    sex: str                      # 'M' or 'F'
    gap_type: str                 # 'numerator' or 'episode'
    missing_element: str
    last_event: dict | None       # engine's last known event, e.g. {"date": "2026-04-15", "value": "145/91", ...}
    exclusion_check: dict | None  # engine's note on whether age-based exclusions can apply
    episode: dict | None          # AAB visit details for episode gaps
    deadline: date
    seed_code: str | None = None  # care_gap_detail.seed_code, for example 'G-0040'; shown in the header


@dataclass(frozen=True)
class PageRef:
    """One converted page that may be sent to the model."""
    document_id: str
    page_no: int
    doc_type: str | None
    document_date: date | None
    source_org_id: str | None
    method: str                   # plain_text | text_layer | ocr_printed | ocr_handwriting | vision
    handwritten: bool
    low_confidence: bool
    text: str


@dataclass(frozen=True)
class Prompt:
    """One model call. A large chart becomes several Prompts; together they cover every selected page."""
    system: str
    user: str
    pages: tuple[PageRef, ...]    # exactly the pages labelled in `user`
    prompt_version: str = PROMPT_VERSION


@dataclass(frozen=True)
class ModelResult:
    """A usable model answer. Empty, cut-off or failed answers raise ModelError instead."""
    text: str                     # the JSON answer, with any <think> block or ``` fence removed
    model: str                    # the model OpenRouter actually used (it can differ from the one asked for)
    duration_ms: int
    prompt_tokens: int | None = None
    output_tokens: int | None = None


class ModelError(Exception):
    """A call that produced no usable answer. `transient` errors may be retried later (timeouts, 5xx, 429)."""

    def __init__(self, message: str, *, transient: bool, raw: str | None = None):
        super().__init__(message)
        self.transient = transient
        self.raw = raw


class UnusableAnswer(ValueError):
    """The answer is not JSON in the OUTPUT_SCHEMA shape. The attempt is failed, never recorded as 'no evidence'."""


class ModelClient(Protocol):
    model: str

    def complete(self, prompt: Prompt) -> ModelResult:
        """Send one prompt and return the answer, or raise ModelError."""
        ...


@dataclass(frozen=True)
class Finding:
    """One finding as the model returned it, before any checks."""
    document_id: str
    page_no: int | None
    excerpt: str
    evidence_kind: str
    evidence_type: str
    service_date: str
    value: str
    confidence: float
    reason: str
    part: str = ""               # 'frailty' or 'advanced_illness' for the two-part frailty exclusions, else ''


@dataclass(frozen=True)
class Candidate:
    """A finding that passed every check, ready to insert into evidence_candidates as PROPOSED.

    excerpt == page.text[char_start:char_end] exactly; the database refuses the row otherwise."""
    document_id: str
    page_no: int
    excerpt: str
    char_start: int
    char_end: int
    evidence_kind: str            # 'numerator' or 'exclusion'
    evidence_type: str            # one of the measure's allowed_evidence_types, normalised
    service_date_raw: str
    service_date_from: date
    service_date_to: date
    value: str
    confidence: float             # 0 to 1
    reason: str                   # the model's explanation, shown to the reviewer as such
    handwritten: bool
    low_confidence: bool
    warnings: tuple[str, ...] = ()
    part: str = ""               # see Finding.part


@dataclass(frozen=True)
class Dropped:
    finding: Finding
    reason: str                   # plain English, stored in extraction_attempts.dropped


@dataclass
class ValidationResult:
    kept: list[Candidate] = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)
