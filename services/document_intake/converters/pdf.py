"""PDF converter: text layer per page via pypdfium2, with word boxes in rendered-image pixels.

Pages with almost no text (scans) are rendered at OCR resolution and sent to converters.image.ocr_image.
"""

import io

import pypdfium2 as pdfium
from PIL import Image

from ..models import CONVERTED, FAILED, MANUAL_REVIEW, OCR_PRINTED, TEXT_LAYER, ConversionResult, Page, Word

PAGE_IMAGE_DPI = 150
OCR_DPI = 300
MIN_TEXT_CHARS = 20   # fewer non-space characters than this means the page is image-only
_PT_PER_INCH = 72.0


def _png(image: Image.Image) -> bytes:
    """Encode a PIL image as PNG bytes."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _render(page: pdfium.PdfPage, dpi: int) -> Image.Image:
    """Render a page to an RGB PIL image at the given dpi."""
    return page.render(scale=dpi / _PT_PER_INCH).to_pil().convert("RGB")


def _normalised_text(raw: str) -> tuple[str, list[int]]:
    """Turn CRLF and CR into LF; return the text and, for each output char, its index in raw."""
    out: list[str] = []
    src: list[int] = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\r":
            out.append("\n")
            src.append(i)
            i += 2 if raw[i + 1 : i + 2] == "\n" else 1
            continue
        out.append(ch)
        src.append(i)
        i += 1
    return "".join(out), src


def _box_to_pixels(
    box: tuple[float, float, float, float], origin: tuple[float, float], height_pt: float, sx: float, sy: float
) -> tuple[float, float, float, float]:
    """Convert a PDF box (left, bottom, right, top; origin bottom-left) to pixel (x0, y0, x1, y1), origin top-left."""
    left, bottom, right, top = box
    return ((left - origin[0]) * sx, (height_pt - (top - origin[1])) * sy,
            (right - origin[0]) * sx, (height_pt - (bottom - origin[1])) * sy)


def _build_words(text: str, src: list[int], boxes: list[tuple[float, float, float, float] | None],
                 size: tuple[int, int]) -> list[Word]:
    """Words are runs of non-space characters; each box is the union of its characters' pixel boxes."""
    words: list[Word] = []
    start: int | None = None
    for i in range(len(text) + 1):
        in_word = i < len(text) and not text[i].isspace()
        if in_word and start is None:
            start = i
        elif not in_word and start is not None:
            found = [b for b in (boxes[src[k]] for k in range(start, i)) if b is not None]
            word = Word(text=text[start:i], char_start=start, char_end=i)
            if found:
                x0, y0 = max(0.0, min(b[0] for b in found)), max(0.0, min(b[1] for b in found))
                x1, y1 = min(float(size[0]), max(b[2] for b in found)), min(float(size[1]), max(b[3] for b in found))
                word.x, word.y, word.width, word.height = x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0)
            words.append(word)
            start = None
    return words


def _char_boxes(textpage: pdfium.PdfTextPage, page: pdfium.PdfPage, raw_len: int,
                size: tuple[int, int]) -> list[tuple[float, float, float, float] | None]:
    """Pixel box for each raw character index; None where pdfium has no box (generated line breaks)."""
    count = textpage.count_chars()
    if count != raw_len or page.get_rotation() != 0:
        return [None] * raw_len  # indices would not line up (surrogate pairs) or the page is rotated
    left, bottom, right, top = page.get_cropbox()
    width_pt, height_pt = right - left, top - bottom
    sx, sy = size[0] / width_pt, size[1] / height_pt
    boxes: list[tuple[float, float, float, float] | None] = []
    for i in range(count):
        box = textpage.get_charbox(i)
        boxes.append(None if box[2] <= box[0] or box[3] <= box[1]
                     else _box_to_pixels(box, (left, bottom), height_pt, sx, sy))
    return boxes


def _text_layer_page(page: pdfium.PdfPage, raw: str, textpage: pdfium.PdfTextPage, page_no: int,
                     image: Image.Image) -> Page:
    """Build a text_layer Page from the pdfium text, with word boxes in the image's pixel space."""
    text, src = _normalised_text(raw)
    boxes = _char_boxes(textpage, page, len(raw), image.size)
    return Page(page_no=page_no, text=text, method=TEXT_LAYER, confidence=None,
                words=_build_words(text, src, boxes, image.size), image_png=_png(image))


def _ocr_page(page: pdfium.PdfPage, page_no: int, image_png: bytes) -> Page:
    """Send an image-only page to the shared OCR entry point at OCR resolution."""
    from . import image as image_ocr  # lazy: the OCR module is owned elsewhere

    result = image_ocr.ocr_image(_render(page, OCR_DPI), page_no)
    result.page_no = page_no
    if result.image_png is None:
        result.image_png = image_png
    return result


def convert(data: bytes, filename: str) -> ConversionResult:
    """Convert a PDF page by page. Corrupt or encrypted files return status failed; this never raises."""
    try:
        document = pdfium.PdfDocument(data)
    except Exception as exc:  # pdfium raises PdfiumError for corrupt and password-protected files
        return ConversionResult(pages=[], status=FAILED, note=f"cannot open PDF: {exc}")
    pages: list[Page] = []
    review: list[int] = []
    try:
        for index in range(len(document)):
            page_no = index + 1
            page = document[index]
            image = _render(page, PAGE_IMAGE_DPI)
            textpage = page.get_textpage()
            raw = textpage.get_text_range()
            if len("".join(raw.split())) >= MIN_TEXT_CHARS:
                pages.append(_text_layer_page(page, raw, textpage, page_no, image))
                continue
            try:
                pages.append(_ocr_page(page, page_no, _png(image)))
            except Exception as exc:  # OCR unavailable or failed: keep the page, flag the document
                pages.append(Page(page_no=page_no, text="", method=OCR_PRINTED, image_png=_png(image),
                                  low_confidence=True))
                review.append(page_no)
                note = f"OCR failed on page {page_no}: {type(exc).__name__}: {exc}"
                if len(review) == 1:
                    first_note = note
    except Exception as exc:  # a page that will not render or parse
        return ConversionResult(pages=[], status=FAILED, note=f"cannot read PDF: {exc}")
    finally:
        document.close()
    if review:
        return ConversionResult(pages=pages, status=MANUAL_REVIEW, note=first_note)
    return ConversionResult(pages=pages, status=CONVERTED)
