"""The page store (US-02 T2): the only place converted pages and words are written.

Every converter returns a ConversionResult; save_conversion() writes it to document_conversion,
document_reference (type), document_pages and page_words, stores page images, classifies the document and links near-duplicates.
"""

import re
from difflib import SequenceMatcher

import psycopg

from .db import audit
from .models import CONVERTED, ConversionResult, Page
from .storage import save_page_image

NEAR_DUPLICATE_RATIO = 0.92

# First matching rule wins. Checked against the lower-cased text of the first page.
_DOC_TYPE_RULES = [
    ("lab report", [r"\blaboratory\b", r"\bhemoglobin a1c\b", r"\begfr\b", r"\bresult report\b", r"\bcollected\b"]),
    ("eye exam letter", [r"\bfundus\b", r"\bretin", r"\boptometr", r"\bophthalm"]),
    ("radiology report", [r"\bmammogra", r"\bbi-rads\b", r"\bimaging\b"]),
    ("discharge summary", [r"\bdischarge summary\b", r"\badmitted\b.*\bdischarged\b"]),
    ("hospice note", [r"\bhospice\b"]),
    ("dialysis letter", [r"dialysis\b"]),
    ("vitals flowsheet", [r"\bflowsheet\b", r"\bvitals\b.*\|"]),
    ("prenatal note", [r"\bprenatal\b", r"\bgestation\b"]),
    ("pharmacy note", [r"\bpharmacy\b", r"\bdispensed\b"]),
    ("progress note", [r"\bprogress note\b", r"\bvisit\b", r"\bfollow-up\b", r"\bpsh\b"]),
]


def classify(text: str) -> str:
    """Rule-based document type from the text. Returns 'other' when nothing matches."""
    lowered = text.lower()
    for doc_type, patterns in _DOC_TYPE_RULES:
        if any(re.search(p, lowered, re.S) for p in patterns):
            return doc_type
    return "other"


def normalize(text: str) -> str:
    """Lower-case, single-spaced text used for duplicate comparison."""
    return re.sub(r"\s+", " ", text).strip().lower()


def _validate_page(page: Page) -> None:
    if page.page_no < 1:
        raise ValueError(f"page_no must be 1 or more, got {page.page_no}")
    for w in page.words:
        if not (0 <= w.char_start < w.char_end <= len(page.text)):
            raise ValueError(f"word offsets {w.char_start}-{w.char_end} outside page {page.page_no} text")


def save_conversion(conn: psycopg.Connection, document_id: str, result: ConversionResult,
                    set_doc_type: bool = False) -> None:
    """Write a conversion result for one document, replacing earlier pages.

    Refuses to replace pages once AI evidence has been proposed against them, because candidates
    point at exact page text and offsets.
    """
    has_candidates = conn.execute(
        "SELECT 1 FROM evidence_proposal_detail WHERE document_reference_id = %s LIMIT 1", (document_id,)).fetchone()
    if has_candidates:
        raise RuntimeError(f"document {document_id} already has evidence proposals; pages cannot be replaced")

    for page in result.pages:
        _validate_page(page)

    conn.execute("DELETE FROM page_words WHERE page_id IN (SELECT id FROM document_pages WHERE document_reference_id = %s)",
                 (document_id,))
    conn.execute("DELETE FROM document_pages WHERE document_reference_id = %s", (document_id,))

    for page in sorted(result.pages, key=lambda p: p.page_no):
        image_path = save_page_image(document_id, page.page_no, page.image_png) if page.image_png else None
        page_id = conn.execute(
            """INSERT INTO document_pages (document_reference_id, page_no, text, method, confidence, image_path, handwritten,
                   low_confidence, ocr_engine)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (document_id, page.page_no, page.text, page.method, page.confidence, image_path, page.handwritten,
             page.low_confidence, page.ocr_engine)).fetchone()[0]
        if page.words:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO page_words (page_id, word_index, text, char_start, char_end, x, y, width, height,
                           confidence) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [(page_id, i, w.text, w.char_start, w.char_end, w.x, w.y, w.width, w.height, w.confidence)
                     for i, w in enumerate(page.words)])

    first_text = result.pages[0].text if result.pages else ""
    conn.execute(
        """UPDATE document_conversion SET page_count = %s, conversion_status = %s, conversion_note = %s
           WHERE document_reference_id = %s""",
        (len(result.pages), result.status, result.note, document_id))
    conn.execute(
        """UPDATE document_reference SET type_code = CASE WHEN %s OR type_code IS NULL THEN %s ELSE type_code END
           WHERE document_reference_id = %s""",
        (set_doc_type, classify(first_text), document_id))

    audit(conn, "document", document_id, "document.converted", {
        "status": result.status, "pages": len(result.pages),
        "methods": sorted({p.method for p in result.pages}),
        "handwritten_pages": [p.page_no for p in result.pages if p.handwritten],
        "low_confidence_pages": [p.page_no for p in result.pages if p.low_confidence],
        "note": result.note,
    })

    if result.status == CONVERTED:
        link_near_duplicate(conn, document_id)


def link_near_duplicate(conn: psycopg.Connection, document_id: str) -> str | None:
    """Link this document to an earlier converted document of the same patient with near-identical text.

    Returns the id it was linked to, or None. Documents already linked are left alone.
    """
    row = conn.execute(
        """SELECT r.patient_id, c.near_duplicate_of FROM document_conversion c
           JOIN document_reference r USING (document_reference_id) WHERE c.document_reference_id = %s""",
        (document_id,)).fetchone()
    if row is None or row[0] is None or row[1] is not None:
        return None
    mine = normalize(_document_text(conn, document_id))
    if not mine:
        return None
    others = conn.execute(
        """SELECT c.document_reference_id FROM document_conversion c
           JOIN document_reference r USING (document_reference_id)
           WHERE r.patient_id = %s AND c.document_reference_id <> %s AND c.conversion_status = %s
               AND c.near_duplicate_of IS NULL ORDER BY c.created_at, c.document_reference_id""",
        (row[0], document_id, CONVERTED)).fetchall()
    for (other_id,) in others:
        theirs = normalize(_document_text(conn, other_id))
        if theirs and is_near_duplicate(mine, theirs):
            conn.execute("UPDATE document_conversion SET near_duplicate_of = %s WHERE document_reference_id = %s",
                         (other_id, document_id))
            audit(conn, "document", document_id, "document.near_duplicate_linked", {"duplicate_of": other_id})
            return other_id
    return None


def is_near_duplicate(a: str, b: str) -> bool:
    """True when two normalized texts are the same document, allowing small differences such as an added
    'via HIE' header line: either the texts are very similar overall, or the lengths are comparable and
    nearly all words of the shorter text appear, in order, in the longer one."""
    if not a or not b:
        return False
    if SequenceMatcher(None, a, b).ratio() >= NEAR_DUPLICATE_RATIO:
        return True
    shorter, longer = sorted((a.split(), b.split()), key=len)
    if len(shorter) / len(longer) < 0.6:
        return False
    matched = sum(block.size for block in SequenceMatcher(None, shorter, longer).get_matching_blocks())
    return matched / len(shorter) >= 0.9


def _document_text(conn: psycopg.Connection, document_id: str) -> str:
    rows = conn.execute("SELECT text FROM document_pages WHERE document_reference_id = %s ORDER BY page_no",
                        (document_id,)).fetchall()
    return "\n".join(r[0] for r in rows)
