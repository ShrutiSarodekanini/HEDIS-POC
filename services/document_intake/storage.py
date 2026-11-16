"""Document bytes in the database, stored as they are (not encrypted).

Uploaded originals are kept in stored_file by their SHA-256, rendered pages in page_image; evidence_library holds the
stored evidence documents the same way. Seed files registered by scripts/db/load_seed.py live in data/seed and are
read in place.
"""

import hashlib

import psycopg

from .config import REPO_ROOT

DB_PREFIX = "db:"
SEED_DIR = (REPO_ROOT / "data" / "seed").resolve()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_original(conn: psycopg.Connection, data: bytes) -> tuple[str, bool]:
    """Store an uploaded file in the caller's transaction. Returns (stored_path, encrypted), encrypted always False."""
    digest = sha256_hex(data)
    conn.execute("""INSERT INTO stored_file (sha256, content, size_bytes) VALUES (%s, %s, %s)
                    ON CONFLICT (sha256) DO NOTHING""", (digest, data, len(data)))
    return DB_PREFIX + digest, False


def read_original(conn: psycopg.Connection, stored_path: str) -> bytes:
    """The bytes of a stored upload, or of a seed file registered under data/seed."""
    if stored_path.startswith(DB_PREFIX):
        row = conn.execute("SELECT content FROM stored_file WHERE sha256 = %s",
                           (stored_path.removeprefix(DB_PREFIX),)).fetchone()
        if row is None:
            raise LookupError(f"stored file {stored_path} is missing from the database")
        return bytes(row[0])
    path = (REPO_ROOT / stored_path).resolve()
    if not path.is_relative_to(SEED_DIR):
        raise ValueError("the file is neither in the database nor a seed file under data/seed")
    return path.read_bytes()


def save_page_image(conn: psycopg.Connection, document_id: str, page_no: int, png_bytes: bytes) -> str:
    """Store a rendered page image (PNG) for the review screen. Returns the value kept in document_pages.image_path."""
    conn.execute("""INSERT INTO page_image (document_reference_id, page_no, content) VALUES (%s, %s, %s)
                    ON CONFLICT (document_reference_id, page_no)
                    DO UPDATE SET content = EXCLUDED.content, created_at = now()""",
                 (document_id, page_no, png_bytes))
    return f"{DB_PREFIX}{document_id}/{page_no}"


def read_page_image(conn: psycopg.Connection, document_id: str, page_no: int) -> bytes | None:
    row = conn.execute("SELECT content FROM page_image WHERE document_reference_id = %s AND page_no = %s",
                       (document_id, page_no)).fetchone()
    return bytes(row[0]) if row else None
