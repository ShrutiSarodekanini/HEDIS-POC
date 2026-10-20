"""Plain text (.txt) converter: one page, text decoded as UTF-8 with cp1252 then latin-1 fallbacks."""

import re

from ..models import CONVERTED, FAILED, PLAIN_TEXT, ConversionResult, Page, Word

_TOKEN = re.compile(r"\S+")


def tokenize_words(text: str) -> list[Word]:
    """Split text on whitespace into Words carrying char offsets into text (no boxes)."""
    return [Word(text=m.group(), char_start=m.start(), char_end=m.end()) for m in _TOKEN.finditer(text)]


def decode_text(data: bytes) -> str:
    """Decode bytes as UTF-8 (BOM tolerated), else cp1252, else latin-1 (which never fails)."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def normalise_newlines(text: str) -> str:
    """Turn CRLF and bare CR into LF so offsets and comparisons are stable."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def convert(data: bytes, filename: str) -> ConversionResult:
    """Convert a .txt file to a single plain_text page."""
    text = normalise_newlines(decode_text(data))
    if not text.strip():
        return ConversionResult(pages=[], status=FAILED, note="text file is empty")
    page = Page(page_no=1, text=text, method=PLAIN_TEXT, words=tokenize_words(text))
    return ConversionResult(pages=[page], status=CONVERTED)
