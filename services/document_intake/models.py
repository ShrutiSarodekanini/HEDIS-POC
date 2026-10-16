"""The common format every converter returns.

A converter turns file bytes into a ConversionResult: a list of Pages, each with its text, how the
text was obtained, a confidence, optional word positions (for highlighting) and an optional page image.
"""

from dataclasses import dataclass, field

# document_pages.method values (database CHECK constraint)
PLAIN_TEXT = "plain_text"          # .txt, DOCX
TEXT_LAYER = "text_layer"          # digital PDF text
OCR_PRINTED = "ocr_printed"        # Tesseract on a scan or photo
OCR_HANDWRITING = "ocr_handwriting"
VISION = "vision"                  # transcribed by a vision model (no word positions)

# documents.conversion_status values
CONVERTED = "converted"
FAILED = "failed"
MANUAL_REVIEW = "manual_review"    # unreadable pages: a person must read the original


@dataclass
class Word:
    """One word on a page. char_start/char_end index into Page.text. Box is in image pixels."""
    text: str
    char_start: int
    char_end: int
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None
    confidence: float | None = None   # 0 to 1


@dataclass
class Page:
    page_no: int                      # 1-based
    text: str
    method: str
    confidence: float | None = None   # 0 to 1; None for text that was not recognised (plain text, text layer)
    words: list[Word] = field(default_factory=list)
    image_png: bytes | None = None    # rendered page image, stored by the document service
    handwritten: bool = False
    low_confidence: bool = False
    ocr_engine: str | None = None     # e.g. "tesseract 5.4.0", "ollama:medgemma:4b"


@dataclass
class ConversionResult:
    pages: list[Page]
    status: str = CONVERTED           # converted | failed | manual_review
    note: str | None = None           # why it failed or needs manual review
