"""Word (.docx) converter: paragraphs and table rows become text, split into pages at explicit page breaks."""

import io

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from ..models import CONVERTED, FAILED, PLAIN_TEXT, ConversionResult, Page
from .text import tokenize_words

_T, _TAB, _BR, _CR = qn("w:t"), qn("w:tab"), qn("w:br"), qn("w:cr")
_BR_TYPE = qn("w:type")
PAGE_BREAK = object()  # sentinel yielded inside a paragraph's pieces


def _paragraph_pieces(paragraph: Paragraph) -> list[object]:
    """Text fragments of a paragraph in order, with PAGE_BREAK where an explicit page break sits."""
    pieces: list[object] = []
    for el in paragraph._p.iter(_T, _TAB, _BR, _CR):
        if el.tag == _T:
            pieces.append(el.text or "")
        elif el.tag == _TAB:
            pieces.append("\t")
        elif el.tag == _BR and el.get(_BR_TYPE) == "page":
            pieces.append(PAGE_BREAK)
        else:
            pieces.append("\n")
    return pieces


def _table_rows(table: Table) -> list[str]:
    """One line per table row, cells joined with ' | ' (merged cells counted once)."""
    lines: list[str] = []
    for row in table.rows:
        cells, seen = [], set()
        for cell in row.cells:
            if id(cell._tc) in seen:
                continue
            seen.add(id(cell._tc))
            cells.append(" ".join(cell.text.split()))
        lines.append(" | ".join(cells))
    return lines


def _split_pages(document: Document) -> list[list[str]]:
    """Walk the body in order and return the lines of each page."""
    pages: list[list[str]] = [[]]
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            current = ""
            for piece in _paragraph_pieces(Paragraph(child, document)):
                if piece is PAGE_BREAK:
                    pages[-1].append(current)
                    pages.append([])
                    current = ""
                else:
                    current += str(piece)
            pages[-1].append(current)
        elif child.tag == qn("w:tbl"):
            pages[-1].extend(_table_rows(Table(child, document)))
    return pages


def convert(data: bytes, filename: str) -> ConversionResult:
    """Convert a .docx file to plain_text pages (no boxes, no images). Never raises."""
    try:
        line_pages = _split_pages(Document(io.BytesIO(data)))
    except Exception as exc:  # corrupt zip, missing parts, bad XML
        return ConversionResult(pages=[], status=FAILED, note=f"cannot read Word file: {type(exc).__name__}: {exc}")
    texts = [text for text in ("\n".join(lines).strip("\n") for lines in line_pages) if text.strip()]
    if not texts:
        return ConversionResult(pages=[], status=FAILED, note="Word file has no text")
    pages = [
        Page(page_no=i, text=t, method=PLAIN_TEXT, words=tokenize_words(t)) for i, t in enumerate(texts, start=1)
    ]
    return ConversionResult(pages=pages, status=CONVERTED)
