"""Document bytes in the database, always encrypted with FILE_ENCRYPTION_KEY.

Uploaded originals are kept in stored_file by the SHA-256 of their plain bytes, rendered pages in page_image, and
evidence_library content is encrypted with the same helpers, so database access alone shows only ciphertext.
Seed files registered by scripts/db/load_seed.py live in data/seed and are read in place.
"""

import hashlib

import psycopg
from cryptography.fernet import Fernet, MultiFernet

from .config import REPO_ROOT, SETTINGS

DB_PREFIX = "db:"
SEED_DIR = (REPO_ROOT / "data" / "seed").resolve()
MISSING_KEY = "FILE_ENCRYPTION_KEY is not set; documents cannot be stored"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cipher() -> MultiFernet:
    """The current key first (used to encrypt), then FILE_ENCRYPTION_OLD_KEYS, which still decrypt."""
    if not SETTINGS.file_encryption_key:
        raise RuntimeError(MISSING_KEY)
    keys = (SETTINGS.file_encryption_key, *SETTINGS.file_encryption_old_keys)
    return MultiFernet([Fernet(key.encode()) for key in keys])


def encrypt(data: bytes) -> bytes:
    return _cipher().encrypt(data)


def decrypt(token: bytes) -> bytes:
    return _cipher().decrypt(bytes(token))


def save_original(conn: psycopg.Connection, data: bytes) -> tuple[str, bool]:
    """Store an uploaded file in the caller's transaction. Returns (stored_path, encrypted)."""
    digest = sha256_hex(data)
    conn.execute("""INSERT INTO stored_file (sha256, content, size_bytes) VALUES (%s, %s, %s)
                    ON CONFLICT (sha256) DO NOTHING""", (digest, encrypt(data), len(data)))
    return DB_PREFIX + digest, True


def read_original(conn: psycopg.Connection, stored_path: str, encrypted: bool) -> bytes:
    """The plain bytes of a stored upload, or of a seed file registered under data/seed."""
    if stored_path.startswith(DB_PREFIX):
        row = conn.execute("SELECT content FROM stored_file WHERE sha256 = %s",
                           (stored_path.removeprefix(DB_PREFIX),)).fetchone()
        if row is None:
            raise LookupError(f"stored file {stored_path} is missing from the database")
        return decrypt(row[0])
    path = (REPO_ROOT / stored_path).resolve()
    if encrypted or not path.is_relative_to(SEED_DIR):
        raise ValueError("the file is neither in the database nor a seed file under data/seed")
    return path.read_bytes()


def save_page_image(conn: psycopg.Connection, document_id: str, page_no: int, png_bytes: bytes) -> str:
    """Store a rendered page image (PNG) for the review screen. Returns the value kept in document_pages.image_path."""
    conn.execute("""INSERT INTO page_image (document_reference_id, page_no, content) VALUES (%s, %s, %s)
                    ON CONFLICT (document_reference_id, page_no)
                    DO UPDATE SET content = EXCLUDED.content, created_at = now()""",
                 (document_id, page_no, encrypt(png_bytes)))
    return f"{DB_PREFIX}{document_id}/{page_no}"


def read_page_image(conn: psycopg.Connection, document_id: str, page_no: int) -> bytes | None:
    row = conn.execute("SELECT content FROM page_image WHERE document_reference_id = %s AND page_no = %s",
                       (document_id, page_no)).fetchone()
    return decrypt(row[0]) if row else None
