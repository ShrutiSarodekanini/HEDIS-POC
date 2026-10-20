"""The intake pipeline (US-02 T1): receive a file, detect and scan it, store it, route it to a converter.

receive_file()    accepts one file: size check, SHA-256 duplicate check, content-based type detection, virus scan,
                  encrypted storage, a source_files row, then process_file().
process_file()    converts one registered file (documents and pages, or structured rows, or unpacks a zip or email)
                  and matches it to a member. One file's failure never stops another.
process_pending() processes every file still waiting, each in its own savepoint.

Every step writes an audit event. Nothing here commits: the caller owns the transaction.
"""

import email
import email.policy
import io
import logging
import mimetypes
import os
import zipfile
from pathlib import PurePosixPath
from typing import Any, Callable

import psycopg
from psycopg.rows import dict_row

from . import scan, storage
from .config import SETTINGS
from .converters import converter_for
from .db import audit
from .detect import ALLOWED_KINDS, detect_kind, mime_for
from .document_service import save_conversion
from .member_match import MATCHED, NEEDS_MATCH, match_member
from .models import FAILED

log = logging.getLogger(__name__)

CONVERTIBLE_KINDS = frozenset({"txt", "pdf", "docx", "jpg", "png", "tiff", "heic"})
STRUCTURED_KINDS = frozenset({"csv", "xlsx"})
CONTAINER_KINDS = frozenset({"zip", "eml"})

MAX_ARCHIVE_DEPTH = 2          # an archive inside an archive inside an archive is refused
MAX_ARCHIVE_ENTRIES = 200
ARCHIVE_EXPANSION_LIMIT = 4    # total unpacked size may be this many times the upload limit

NOT_STORED = "not_stored"      # stored_path for a file whose bytes were refused (infected, scan failed)

FILE_COLUMNS = ("id, sha256, original_name, stored_path, detected_type, detected_kind, size_bytes, received_at, "
                "source_org_id, member_id, match_status, processing_status, processing_note, encrypted, "
                "parent_file_id, scan_result")


# ---------------------------------------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------------------------------------

def get_file(conn: psycopg.Connection, source_file_id: int) -> dict | None:
    """One source_files row (no bytes), or None."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(f"SELECT {FILE_COLUMNS} FROM source_files WHERE id = %s", (source_file_id,)).fetchone()


def list_files(conn: psycopg.Connection, status: str | None = None, match_status: str | None = None,
               limit: int = 500, offset: int = 0) -> list[dict]:
    """source_files rows, newest id last, optionally filtered by processing_status and match_status."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            f"""SELECT {FILE_COLUMNS} FROM source_files
                WHERE (%s::text IS NULL OR processing_status = %s) AND (%s::text IS NULL OR match_status = %s)
                ORDER BY id LIMIT %s OFFSET %s""",
            (status, status, match_status, match_status, limit, offset)).fetchall()


def get_document(conn: psycopg.Connection, document_id: str) -> dict | None:
    """A document with its pages (page_no, method, confidence, handwritten, low_confidence, text, image_path)."""
    docs = get_documents(conn, [document_id])
    return docs[0] if docs else None


def get_documents(conn: psycopg.Connection, document_ids: list[str]) -> list[dict]:
    """The documents that exist among document_ids, each with its pages, in the order asked.

    Two queries however many ids are asked for. Unknown ids are left out."""
    with conn.cursor(row_factory=dict_row) as cur:
        docs = cur.execute(
            """SELECT id, source_file_id, member_id, doc_type, document_date, source_org_id, near_duplicate_of,
                      page_count, conversion_status, conversion_note FROM documents WHERE id = ANY(%s)""",
            (document_ids,)).fetchall()
        pages = cur.execute(
            """SELECT document_id, page_no, method, confidence, handwritten, low_confidence, text, image_path,
                      ocr_engine
               FROM document_pages WHERE document_id = ANY(%s) ORDER BY document_id, page_no""",
            (document_ids,)).fetchall()
    by_id = {doc["id"]: {**doc, "pages": []} for doc in docs}
    for page in pages:
        by_id[page.pop("document_id")]["pages"].append(page)
    return [by_id[i] for i in document_ids if i in by_id]


def match_queue(conn: psycopg.Connection) -> list[dict]:
    """Files that need a person to pick the member: {file, document_id, text_preview, candidates}."""
    items = []
    for f in list_files(conn, match_status=NEEDS_MATCH):
        if f["processing_status"] == "rejected":
            continue
        doc = conn.execute("SELECT id FROM documents WHERE source_file_id = %s ORDER BY id LIMIT 1",
                           (f["id"],)).fetchone()
        text = ""
        if doc:
            text = "\n".join(r[0] for r in conn.execute(
                "SELECT text FROM document_pages WHERE document_id = %s ORDER BY page_no", (doc[0],)).fetchall())
        items.append({"file": f, "document_id": doc[0] if doc else None, "text_preview": text[:1000],
                      "candidates": match_member(conn, text).candidates})
    return items


# ---------------------------------------------------------------------------------------------------------------
# Receiving
# ---------------------------------------------------------------------------------------------------------------

def receive_file(conn: psycopg.Connection, data: bytes, filename: str, source_org_id: str | None = None,
                 member_id: str | None = None, parent_file_id: int | None = None) -> dict:
    """Accept one file and run it through the pipeline.

    Returns a dict whose "status" is one of:
      received   registered and processed (see processing_status), "children" lists unpacked files;
      duplicate  the same bytes were received before, nothing new recorded (source_file_id is the earlier file);
      rejected   registered as rejected (reason: unsupported_type, infected or scan_failed);
      too_large  over the upload limit, nothing stored.
    Raises ValueError for an unknown source_org_id or member_id.
    """
    name = _clean_name(filename)
    limit = SETTINGS.max_upload_bytes
    if len(data) > limit:
        message = f"File is larger than the {limit // (1024 * 1024)} MB upload limit."
        audit(conn, "upload", name, "file.rejected", {"reason": "file_too_large", "size_bytes": len(data),
                                                      "limit_bytes": limit, "parent_file_id": parent_file_id})
        return {"status": "too_large", "filename": name, "reason": "file_too_large", "message": message}
    _check_references(conn, source_org_id, member_id)

    digest = storage.sha256_hex(data)
    existing = conn.execute("SELECT id FROM source_files WHERE sha256 = %s", (digest,)).fetchone()
    if existing:
        return _duplicate(conn, existing[0], name, parent_file_id)

    kind = detect_kind(data, name)
    scan_result = scan.scan_bytes(data, name)
    reason, note = _refusal(kind, scan_result)
    if reason in ("infected", "scan_failed"):
        stored_path, encrypted = NOT_STORED, False
    else:
        stored_path, encrypted = storage.save_original(data)

    keeps_member = member_id is not None or kind in CONTAINER_KINDS or kind in STRUCTURED_KINDS
    match_status = "unmatched" if reason else ("matched" if keeps_member else NEEDS_MATCH)
    row = conn.execute(
        """INSERT INTO source_files (sha256, original_name, stored_path, detected_type, detected_kind, size_bytes,
               source_org_id, member_id, match_status, processing_status, processing_note, encrypted, parent_file_id,
               scan_result)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (sha256) DO NOTHING RETURNING id""",
        (digest, name, stored_path, mime_for(kind), kind, len(data), source_org_id, member_id, match_status,
         "rejected" if reason else "pending", note, encrypted, parent_file_id, scan_result)).fetchone()
    if row is None:  # another upload of the same bytes won the race
        existing = conn.execute("SELECT id FROM source_files WHERE sha256 = %s", (digest,)).fetchone()
        return _duplicate(conn, existing[0], name, parent_file_id)
    file_id = row[0]

    claimed = mimetypes.guess_type(name)[0]
    audit(conn, "source_file", str(file_id), "file.received", {
        "name": name, "sha256": digest, "size_bytes": len(data), "detected_kind": kind, "detected_type": mime_for(kind),
        "claimed_type": claimed, "extension_mismatch": bool(claimed and claimed != mime_for(kind)),
        "encrypted": encrypted, "parent_file_id": parent_file_id, "source_org_id": source_org_id,
        "member_id": member_id})
    audit(conn, "source_file", str(file_id), "file.scanned", {"result": scan_result})

    if reason:
        audit(conn, "source_file", str(file_id), "file.rejected", {"reason": reason, "note": note})
        return {**_summary(conn, file_id), "status": "rejected", "reason": reason, "message": note, "children": []}
    result = process_file(conn, file_id)
    return {**result, "status": "received", "filename": name}


def _refusal(kind: str, scan_result: str) -> tuple[str | None, str | None]:
    if scan_result == scan.INFECTED:
        return "infected", "Virus scan found a threat. The file was not stored and will not be converted."
    if scan_result == scan.SCAN_FAILED and os.environ.get("INTAKE_REQUIRE_SCAN") == "1":
        return "scan_failed", "The virus scan could not complete, so the file was not accepted. Send it again."
    if kind not in ALLOWED_KINDS:
        return "unsupported_type", "Unsupported file type (checked from the file content, not the extension)."
    return None, None


def _duplicate(conn: psycopg.Connection, existing_id: int, name: str, parent_file_id: int | None) -> dict:
    audit(conn, "source_file", str(existing_id), "file.duplicate_received",
          {"name": name, "parent_file_id": parent_file_id})
    return {"status": "duplicate", "source_file_id": existing_id, "filename": name, "children": [],
            "message": f"The same file was received before (file {existing_id}). Nothing new was recorded."}


def _check_references(conn: psycopg.Connection, source_org_id: str | None, member_id: str | None) -> None:
    if source_org_id and not conn.execute("SELECT 1 FROM organizations WHERE id = %s", (source_org_id,)).fetchone():
        raise ValueError(f"unknown source_org_id {source_org_id}")
    if member_id and not conn.execute("SELECT 1 FROM members WHERE member_id = %s", (member_id,)).fetchone():
        raise ValueError(f"unknown member_id {member_id}")


def _clean_name(filename: str) -> str:
    name = PurePosixPath((filename or "").replace("\\", "/")).name.strip()
    return name or "unnamed"


# ---------------------------------------------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------------------------------------------

def process_file(conn: psycopg.Connection, source_file_id: int) -> dict:
    """Process one registered file. Never raises for a bad file: the failure is recorded on the row.

    Files already done, rejected or duplicate are left alone. Raises LookupError for an unknown id.
    """
    row = get_file(conn, source_file_id)
    if row is None:
        raise LookupError(f"file {source_file_id} not found")
    if row["processing_status"] in ("done", "rejected", "duplicate"):
        return _summary(conn, source_file_id, skipped=True)
    try:
        with conn.transaction():  # a savepoint: a failure leaves nothing half-written
            return _process(conn, row)
    except Exception as exc:  # noqa: BLE001 - any failure is recorded on the file and processing moves on
        note = f"{type(exc).__name__}: {exc}"[:500]
        log.warning("processing file %s failed: %s", source_file_id, note)
        conn.execute("UPDATE source_files SET processing_status = 'failed', processing_note = %s WHERE id = %s",
                     (note, source_file_id))
        audit(conn, "source_file", str(source_file_id), "file.process_failed", {"error": note})
        return _summary(conn, source_file_id)


def _process(conn: psycopg.Connection, row: dict) -> dict:
    file_id = row["id"]
    conn.execute("UPDATE source_files SET processing_status = 'processing' WHERE id = %s", (file_id,))
    audit(conn, "source_file", str(file_id), "file.processing_started", None)
    data = storage.read_original(row["stored_path"], row["encrypted"])

    kind = row["detected_kind"]
    if kind is None:  # seed files are registered by extension; detect from the content now
        kind = detect_kind(data, row["original_name"])
        conn.execute("UPDATE source_files SET detected_kind = %s, detected_type = %s WHERE id = %s",
                     (kind, mime_for(kind), file_id))
        row = {**row, "detected_kind": kind}

    children: list[dict] = []
    structured: dict | None = None
    if kind not in ALLOWED_KINDS:
        status, note = "rejected", "Unsupported file type (checked from the file content, not the extension)."
    elif kind in CONTAINER_KINDS:
        children = _unpack(conn, row, data, kind)
        status, note = "done", f"unpacked {len(children)} file(s)" if children else "no files inside"
    elif kind in STRUCTURED_KINDS:
        structured = _structured_loader()(conn, file_id, data, row["original_name"])
        status, note = _structured_outcome(structured)
    else:
        status, note = _convert(conn, row, data, kind)

    conn.execute("UPDATE source_files SET processing_status = %s, processing_note = %s WHERE id = %s",
                 (status, note, file_id))
    audit(conn, "source_file", str(file_id), "file.processed", {"status": status, "note": note, "kind": kind})
    summary = _summary(conn, file_id, children=children)
    if structured is not None:
        summary["structured"] = {k: v for k, v in structured.items() if k not in ("errors", "headers", "suggestion")}
    return summary


def _structured_outcome(result: dict) -> tuple[str, str | None]:
    """Map the structured loader's result to the file's final status and note."""
    if result.get("status") == "loaded":
        return "done", (f"structured: {result.get('loaded', 0)} loaded, {result.get('rejected', 0)} rejected, "
                        f"{result.get('free_text_to_ai', 0)} to AI")
    if result.get("status") == "needs_mapping":
        return "failed", "unknown layout: confirm a mapping"
    return "failed", f"could not read file: {result.get('reason', 'unknown error')}"


def _structured_loader() -> Callable[..., dict]:
    from .structured import load_structured_file  # imported late: the module belongs to another task
    return load_structured_file


def _convert(conn: psycopg.Connection, row: dict, data: bytes, kind: str) -> tuple[str, str | None]:
    """Convert a document, match it to a member if it has none, and save the pages."""
    file_id = row["id"]
    result = converter_for(kind)(data, row["original_name"])
    text = "\n".join(p.text for p in result.pages)

    member_id = row["member_id"]
    if member_id is None:
        match = match_member(conn, text)
        if match.status == MATCHED:
            member_id = match.member_id
            conn.execute("UPDATE source_files SET member_id = %s, match_status = 'matched' WHERE id = %s",
                         (member_id, file_id))
            audit(conn, "source_file", str(file_id), "file.member_matched",
                  {"member_id": member_id, "method": match.method})
        else:
            conn.execute("UPDATE source_files SET match_status = 'needs_match' WHERE id = %s", (file_id,))
            audit(conn, "source_file", str(file_id), "file.needs_match",
                  {"candidates": [c["member_id"] for c in match.candidates]})

    doc = conn.execute("SELECT id, member_id FROM documents WHERE source_file_id = %s ORDER BY created_at, id LIMIT 1",
                       (file_id,)).fetchone()
    if doc is None:
        doc_id = f"U-{file_id}"
        conn.execute("INSERT INTO documents (id, source_file_id, member_id, source_org_id) VALUES (%s, %s, %s, %s)",
                     (doc_id, file_id, member_id, row["source_org_id"]))
    else:
        doc_id = doc[0]
        if member_id and doc[1] is None:
            conn.execute("UPDATE documents SET member_id = %s WHERE id = %s", (member_id, doc_id))

    save_conversion(conn, doc_id, result)
    if result.status == FAILED:
        return "failed", result.note or "Conversion failed."
    return "done", result.note


# ---------------------------------------------------------------------------------------------------------------
# Zip and email bundles
# ---------------------------------------------------------------------------------------------------------------

def _unpack(conn: psycopg.Connection, row: dict, data: bytes, kind: str) -> list[dict]:
    if _depth(conn, row["id"]) >= MAX_ARCHIVE_DEPTH:
        raise ValueError(f"archives nested more than {MAX_ARCHIVE_DEPTH} levels deep are not unpacked")
    member_id = row["member_id"]
    if kind == "zip":
        entries = _zip_entries(data)
    else:
        entries, mail_member = _eml_entries(conn, data)
        if member_id is None and mail_member:
            member_id = mail_member
            conn.execute("UPDATE source_files SET member_id = %s WHERE id = %s", (member_id, row["id"]))
            audit(conn, "source_file", str(row["id"]), "file.member_matched",
                  {"member_id": member_id, "method": "email_text"})

    results = []
    for name, payload in entries:
        if isinstance(payload, Exception):
            results.append({"status": "error", "filename": name, "message": str(payload)})
            audit(conn, "source_file", str(row["id"]), "file.child_failed", {"name": name, "error": str(payload)})
            continue
        try:
            with conn.transaction():
                results.append(receive_file(conn, payload, name, row["source_org_id"], member_id, row["id"]))
        except Exception as exc:  # noqa: BLE001 - one bad entry must not stop the rest of the bundle
            results.append({"status": "error", "filename": name, "message": f"{type(exc).__name__}: {exc}"})
            audit(conn, "source_file", str(row["id"]), "file.child_failed",
                  {"name": name, "error": f"{type(exc).__name__}: {exc}"})
    audit(conn, "source_file", str(row["id"]), "file.unpacked",
          {"kind": kind, "entries": [{"name": r.get("filename"), "status": r["status"],
                                      "source_file_id": r.get("source_file_id")} for r in results]})
    return results


def _depth(conn: psycopg.Connection, file_id: int) -> int:
    """How many archives this file is nested inside."""
    depth, current = 0, file_id
    while depth <= MAX_ARCHIVE_DEPTH + 1:
        parent = conn.execute("SELECT parent_file_id FROM source_files WHERE id = %s", (current,)).fetchone()
        if not parent or parent[0] is None:
            break
        depth, current = depth + 1, parent[0]
    return depth


def _zip_entries(data: bytes) -> list[tuple[str, bytes | Exception]]:
    limit = SETTINGS.max_upload_bytes
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = [i for i in zf.infolist()
                 if not i.is_dir() and not i.filename.startswith("__MACOSX/") and not _clean_name(i.filename).startswith(".")]
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise ValueError(f"archive has {len(infos)} files; the limit is {MAX_ARCHIVE_ENTRIES}")
        if sum(i.file_size for i in infos) > limit * ARCHIVE_EXPANSION_LIMIT:
            raise ValueError("archive is too large once unpacked")
        out: list[tuple[str, bytes | Exception]] = []
        for info in infos:
            name = _clean_name(info.filename)
            try:
                out.append((name, zf.read(info)))
            except (RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:  # password-protected or damaged
                out.append((name, ValueError(f"could not be read from the archive: {exc}")))
        return out


def _eml_entries(conn: psycopg.Connection, data: bytes) -> tuple[list[tuple[str, bytes | Exception]], str | None]:
    """Attachments of an email, plus a member found in its subject and body (or None)."""
    msg = email.message_from_bytes(data, policy=email.policy.default)
    out: list[tuple[str, bytes | Exception]] = []
    for n, part in enumerate(msg.iter_attachments(), start=1):
        name = _clean_name(part.get_filename() or f"attachment-{n}")
        payload = part.get_payload(decode=True)
        if payload is None:  # an attached message
            payload = part.as_bytes()
        out.append((name, payload))

    text = str(msg.get("Subject", ""))
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is not None:
        try:
            text += "\n" + body.get_content()[:20000]
        except (LookupError, ValueError):
            log.warning("email body could not be decoded for member matching")
    match = match_member(conn, text)
    return out, match.member_id if match.status == MATCHED else None


# ---------------------------------------------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------------------------------------------

def process_pending(conn: psycopg.Connection, limit: int | None = None) -> dict:
    """Process every file with processing_status 'pending'. Returns counts and one line per file."""
    sql = "SELECT id FROM source_files WHERE processing_status = 'pending' ORDER BY id"
    ids = [r[0] for r in conn.execute(sql + (" LIMIT %s" if limit else ""), (limit,) if limit else ()).fetchall()]
    counts: dict[str, Any] = {"selected": len(ids), "done": 0, "failed": 0, "rejected": 0, "skipped": 0,
                              "needs_match": 0, "results": []}
    for file_id in ids:
        row = get_file(conn, file_id)
        if row is None or row["processing_status"] != "pending":  # already handled as part of a bundle
            counts["skipped"] += 1
            continue
        res = process_file(conn, file_id)
        outcome = res["processing_status"]
        counts[outcome] = counts.get(outcome, 0) + 1
        if res["match_status"] == NEEDS_MATCH and outcome == "done":
            counts["needs_match"] += 1
        counts["results"].append({"source_file_id": file_id, "filename": res["filename"],
                                  "processing_status": outcome, "match_status": res["match_status"],
                                  "note": res["processing_note"]})
    return counts


def _summary(conn: psycopg.Connection, file_id: int, children: list[dict] | None = None,
             skipped: bool = False) -> dict:
    row = get_file(conn, file_id)
    doc = conn.execute("SELECT id FROM documents WHERE source_file_id = %s ORDER BY id LIMIT 1", (file_id,)).fetchone()
    return {
        "source_file_id": file_id,
        "filename": row["original_name"],
        "kind": row["detected_kind"],
        "processing_status": row["processing_status"],
        "processing_note": row["processing_note"],
        "match_status": row["match_status"],
        "member_id": row["member_id"],
        "scan_result": row["scan_result"],
        "document_id": doc[0] if doc else None,
        "children": children or [],
        "skipped": skipped,
    }
