"""Converters turn file bytes into a ConversionResult (see models.py).

Every converter module exposes:

    def convert(data: bytes, filename: str) -> ConversionResult

Registry by detected file kind (see intake detection):
    txt  -> converters.text
    pdf  -> converters.pdf      (text layer; image-only pages are sent to OCR)
    docx -> converters.docx
    jpg, png, tiff, heic -> converters.image   (printed OCR, with handwriting fallback)

Shared OCR entry point used by the PDF converter for image-only pages:
    converters.image.ocr_image(image: PIL.Image.Image, page_no: int) -> Page

CSV and XLSX are not converted to pages: services/document_intake/structured.py loads them as coded rows.
"""

from importlib import import_module
from typing import Callable

from ..models import ConversionResult

_MODULE_BY_KIND = {
    "txt": "text",
    "pdf": "pdf",
    "docx": "docx",
    "jpg": "image",
    "png": "image",
    "tiff": "image",
    "heic": "image",
}


def converter_for(kind: str) -> Callable[[bytes, str], ConversionResult]:
    """Return the convert() function for a detected file kind, or raise KeyError if none exists."""
    module = import_module(f"{__name__}.{_MODULE_BY_KIND[kind]}")
    return module.convert
