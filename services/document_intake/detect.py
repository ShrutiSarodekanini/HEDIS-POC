"""File type detection from content (magic bytes), never from the file extension."""

import csv
import io
import re
import zipfile

KINDS = ("pdf", "jpg", "png", "tiff", "heic", "docx", "xlsx", "csv", "txt", "zip", "eml", "unknown")

#: Kinds the intake accepts. Everything except "unknown" is on the allow-list.
ALLOWED_KINDS = frozenset(k for k in KINDS if k != "unknown")

_MIME = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "png": "image/png",
    "tiff": "image/tiff",
    "heic": "image/heic",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "txt": "text/plain",
    "zip": "application/zip",
    "eml": "message/rfc822",
    "unknown": "application/octet-stream",
}

_HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}
_EML_HEADER = re.compile(r"^(from|to|subject|mime-version|date|received|return-path|message-id|cc):", re.I)
_EML_FIRST = re.compile(r"^(from|to|subject|mime-version|received|return-path):", re.I)
_HEADER_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:[ \t]")
_SNIFF_BYTES = 64 * 1024
_CSV_LINES = 20
_NUL = bytes([0])
_UTF16_BOMS = (bytes([0xFF, 0xFE]), bytes([0xFE, 0xFF]))
_JPEG = bytes([0xFF, 0xD8, 0xFF])
_PNG = bytes([0x89]) + b"PNG\r\n" + bytes([0x1A]) + b"\n"
_TIFF_LE = b"II*" + _NUL
_TIFF_BE = b"MM" + _NUL + b"*"
_ZIP = b"PK" + bytes([3, 4])


def mime_for(kind: str) -> str:
    """The MIME type recorded for a detected kind."""
    return _MIME.get(kind, _MIME["unknown"])


def detect_kind(data: bytes, filename: str) -> str:
    """Return the real kind of a file: pdf, jpg, png, tiff, heic, docx, xlsx, csv, txt, zip, eml or unknown.

    Only the content is used. The filename is accepted for the caller's convenience and ignored, so a JPEG
    named .pdf is reported as jpg.
    """
    head = data[:16]
    if data.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(_JPEG):
        return "jpg"
    if head.startswith(_PNG):
        return "png"
    if head[:4] in (_TIFF_LE, _TIFF_BE):
        return "tiff"
    if head[4:8] == b"ftyp" and head[8:12] in _HEIC_BRANDS:
        return "heic"
    if head.startswith(_ZIP):
        return _detect_zip(data)
    text = _decode_text(data)
    if text is None:
        return "unknown"
    if _looks_like_eml(text):
        return "eml"
    if _looks_like_csv(text):
        return "csv"
    return "txt" if text.strip() else "unknown"


def _detect_zip(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        return "unknown"
    if any(n.startswith("word/") for n in names):
        return "docx"
    if any(n.startswith("xl/") for n in names):
        return "xlsx"
    return "zip"


def _decode_text(data: bytes) -> str | None:
    """Decode the start of a plain-text file, or return None when the bytes look binary."""
    chunk = data[:_SNIFF_BYTES]
    if chunk.startswith(_UTF16_BOMS):
        return chunk.decode("utf-16", errors="ignore")
    if _NUL in chunk:
        return None
    try:
        text = chunk.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        if len(data) > len(chunk) and exc.start >= len(chunk) - 3:  # a character cut by the sample size
            text = chunk[:exc.start].decode("utf-8-sig")
        else:
            text = chunk.decode("cp1252", errors="replace")
    controls = sum(1 for ch in text if ord(ch) < 32 and ch not in "\r\n\t\f")
    if text and controls / len(text) > 0.02:
        return None
    return text


def _looks_like_eml(text: str) -> bool:
    lines = text.lstrip("﻿").splitlines()
    if not lines or not _EML_FIRST.match(lines[0]):
        return False
    headers = 0
    for line in lines:
        if not line.strip():
            break
        if line[0] in " \t":  # folded header continuation
            continue
        if _HEADER_LINE.match(line):
            headers += 1
        else:
            return False
    return headers >= 2 and any(_EML_HEADER.match(line) for line in lines[1:headers + 1])


def _looks_like_csv(text: str) -> bool:
    """CSV when the first lines share one delimiter and the same column count of 2 or more."""
    lines = [ln for ln in text[:_SNIFF_BYTES].splitlines() if ln.strip()]
    if len(text) >= _SNIFF_BYTES and len(lines) > 1:
        lines = lines[:-1]  # the sample may have cut the last line
    lines = lines[:_CSV_LINES]
    if len(lines) < 2:
        return False
    sample = "\n".join(lines)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return False
    try:
        counts = {len(row) for row in csv.reader(io.StringIO(sample), dialect)}
    except csv.Error:
        return False
    return len(counts) == 1 and next(iter(counts)) >= 2
