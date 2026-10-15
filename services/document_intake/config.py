"""Settings for document intake, read from the repo-root .env and environment variables."""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")


def _default_tesseract() -> str:
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in (
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
        Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
    ):
        if candidate.exists():
            return str(candidate)
    return "tesseract"


@dataclass(frozen=True)
class Settings:
    database_url: str
    store_dir: Path                 # encrypted originals and page images for uploaded files
    max_upload_bytes: int
    file_encryption_key: str | None  # Fernet key; uploads are stored encrypted when set
    tesseract_cmd: str
    ocr_low_confidence: float       # page confidence below this is flagged low_confidence
    handwriting_confidence: float   # printed-OCR confidence below this triggers handwriting recognition
    ollama_url: str
    vision_model: str               # local vision model used for handwriting transcription
    defender_cmd: str | None        # Windows Defender command-line scanner, if present
    actor: str                      # recorded in audit events for automated steps


def load_settings() -> Settings:
    url = os.environ.get("DATABASE_URL", "")
    defender = Path("C:/Program Files/Windows Defender/MpCmdRun.exe")
    return Settings(
        database_url=url.replace("postgresql+psycopg://", "postgresql://", 1),
        store_dir=Path(os.environ.get("INTAKE_STORE_DIR", REPO_ROOT / "data" / "intake_store")),
        max_upload_bytes=int(os.environ.get("INTAKE_MAX_UPLOAD_MB", "25")) * 1024 * 1024,
        file_encryption_key=os.environ.get("FILE_ENCRYPTION_KEY") or None,
        tesseract_cmd=os.environ.get("TESSERACT_CMD") or _default_tesseract(),
        ocr_low_confidence=float(os.environ.get("OCR_LOW_CONFIDENCE", "0.75")),
        handwriting_confidence=float(os.environ.get("OCR_HANDWRITING_CONFIDENCE", "0.55")),
        ollama_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        vision_model=os.environ.get("VISION_MODEL", "medgemma:4b"),
        defender_cmd=str(defender) if defender.exists() else None,
        actor=os.environ.get("INTAKE_ACTOR", "document_intake"),
    )


SETTINGS = load_settings()
