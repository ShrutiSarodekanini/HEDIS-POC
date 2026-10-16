"""File storage: originals (encrypted when a key is configured) and page images.

Seed files registered by scripts/db/load_seed.py live in data/seed and are read in place.
Uploaded files are stored under SETTINGS.store_dir by their SHA-256 hash.
"""

import hashlib
from pathlib import Path

from cryptography.fernet import Fernet

from .config import REPO_ROOT, SETTINGS


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fernet() -> Fernet | None:
    return Fernet(SETTINGS.file_encryption_key.encode()) if SETTINGS.file_encryption_key else None


def save_original(data: bytes) -> tuple[str, bool]:
    """Store an uploaded file. Returns (stored_path relative to the repo, encrypted)."""
    digest = sha256_hex(data)
    fernet = _fernet()
    folder = SETTINGS.store_dir / "originals" / digest[:2]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (digest + (".enc" if fernet else ".bin"))
    if not path.exists():
        path.write_bytes(fernet.encrypt(data) if fernet else data)
    return _relative(path), bool(fernet)


def read_original(stored_path: str, encrypted: bool) -> bytes:
    """Read a stored file back, decrypting if needed."""
    raw = (REPO_ROOT / stored_path).read_bytes()
    if not encrypted:
        return raw
    fernet = _fernet()
    if fernet is None:
        raise RuntimeError("File is encrypted but FILE_ENCRYPTION_KEY is not set.")
    return fernet.decrypt(raw)


def save_page_image(document_id: str, page_no: int, png_bytes: bytes) -> str:
    """Store a rendered page image (PNG) for the review screen. Returns the path relative to the repo."""
    folder = SETTINGS.store_dir / "page_images" / document_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"page-{page_no:03d}.png"
    path.write_bytes(png_bytes)
    return _relative(path)


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()
