"""OCR and handwriting engine comparison for US-02 S-37 (see docs/decisions/0001-ocr-and-handwriting-engines.md).

Measures, on every image document in data/seed/documents, against manifest.json ground_truth_text:
  - character error rate (CER): Levenshtein distance / ground-truth length, lower-cased, whitespace-normalised
  - line CER: the same per ground-truth line against its best match anywhere in the text (ignores extra lines)
  - whether the key value survives in the text
  - seconds per page
for raw Tesseract (grayscale, no clean-up), Tesseract after clean-up (converters.image.prepare), and the
local vision model on the handwritten pages, the blurred page and the worst printed page, and the full
pipeline (clean-up, then the vision model when the page needs it).

Run from the repo root:
    python scripts/ocr_eval.py                 # printed engines only (about 1 minute)
    python scripts/ocr_eval.py --vision        # also the vision model (about 2 minutes per call on CPU)
    python scripts/ocr_eval.py --docs D-903 D-601 --vision
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pypdfium2 as pdfium  # noqa: E402
import pytesseract  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

from services.document_intake.config import SETTINGS  # noqa: E402
from services.document_intake.converters import image as image_conv  # noqa: E402

DOCS = ROOT / "data" / "seed" / "documents"
PDF_DPI = 200

#: key value that must survive, per document (case-insensitive, whitespace-normalised)
KEY_VALUES = {
    "D-301": "bilateral mastectomy",
    "D-502": "Pap smear 2021",
    "D-903": "131/79",
    "D-1701": "Pregnancy confirmed",
    "D-9001": "126/80",
    "D-601": "Total abdominal hysterectomy",
    "D-1401": "134/82",
    "D-1102": "125/80",
    "D-1501": "hemodialysis",
    "D-202": "Screening mammogram 2025-11-03",
    "D-2101": "metastatic pancreatic cancer",
}
#: for multi-page files, the 1-based page the ground truth describes (D-1501 page 1 is a fax cover sheet)
GROUND_TRUTH_PAGE = {"D-1501": 2}
#: handwritten pages, the blurred page, and the page raw Tesseract reads worst (a tilted table photo)
VISION_DOCS = ("D-601", "D-1401", "D-9001", "D-903")


def norm(text: str) -> str:
    """Lower-case and collapse whitespace."""
    return re.sub(r"\s+", " ", text.lower()).strip()


def levenshtein(a: str, b: str) -> int:
    """Edit distance with a rolling row."""
    if not a:
        return len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(truth: str, text: str) -> float:
    """Character error rate of text against truth (can exceed 1.0 when text has extra characters)."""
    t, x = norm(truth), norm(text)
    return levenshtein(t, x) / max(len(t), 1)


def _best_substring_distance(needle: str, hay: str) -> int:
    """Smallest edit distance between needle and any substring of hay (Sellers algorithm)."""
    prev = [0] * (len(hay) + 1)
    for i, cn in enumerate(needle, 1):
        cur = [i]
        for j, ch in enumerate(hay, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (cn != ch)))
        prev = cur
    return min(prev)


def line_cer(truth: str, text: str) -> float:
    """CER of each ground-truth line against its best match anywhere in the text, so extra lines
    (screenshot menus, headers) are not counted as errors. Length-weighted over lines."""
    hay = norm(text)
    lines = [norm(ln) for ln in truth.splitlines() if norm(ln)]
    errors = sum(_best_substring_distance(ln, hay) for ln in lines)
    return errors / max(sum(len(ln) for ln in lines), 1)


def has_key(doc_id: str, text: str) -> bool:
    return norm(KEY_VALUES[doc_id]) in norm(text)


def load_pages(entry: dict) -> list[Image.Image]:
    """Page images of a manifest entry: image frames, or rendered pages for a scanned PDF."""
    path = DOCS / entry["file"]
    if path.suffix == ".pdf":
        pdf = pdfium.PdfDocument(str(path))
        return [pdf[i].render(scale=PDF_DPI / 72).to_pil() for i in range(len(pdf))]
    img = Image.open(path)
    frames = []
    for i in range(getattr(img, "n_frames", 1)):
        img.seek(i)
        frames.append(img.copy())
    return frames


def raw_tesseract(img: Image.Image) -> str:
    """Tesseract on the plain grayscale image: no clean-up, psm 6."""
    gray = ImageOps.exif_transpose(img).convert("L")
    return pytesseract.image_to_string(gray, lang="eng", config="--psm 6 -c preserve_interword_spaces=1")


def timed(fn, *args, **kwargs):
    start = time.perf_counter()
    out = fn(*args, **kwargs)
    return out, time.perf_counter() - start


def evaluate(entry: dict, with_vision: bool) -> list[dict]:
    """One result row per engine for a document."""
    doc_id, truth = entry["doc_id"], entry["ground_truth_text"]
    pages = load_pages(entry)
    gt_page = GROUND_TRUTH_PAGE.get(doc_id)
    pick = (lambda texts: texts[gt_page - 1]) if gt_page else (lambda texts: "\n".join(texts))
    rows = []

    raw, sec = zip(*[timed(raw_tesseract, p) for p in pages])
    rows.append(_row(doc_id, "tesseract raw", pick(list(raw)), truth, sum(sec) / len(pages)))

    clean, sec = zip(*[timed(image_conv.ocr_image, p, i, use_vision=False) for i, p in enumerate(pages, 1)])
    texts = [pg.text for pg in clean]
    row = _row(doc_id, "tesseract + clean-up", pick(texts), truth, sum(sec) / len(pages))
    row["conf"] = round(clean[(gt_page or 1) - 1].confidence or 0.0, 2)
    rows.append(row)

    if with_vision and doc_id in VISION_DOCS:
        page = pages[0]
        text, s = timed(image_conv.transcribe_with_vision, page, crop=False)
        rows.append(_row(doc_id, "vision, original photo", text or "", truth, s))
        done, s = timed(image_conv.ocr_image, page, 1)
        row = _row(doc_id, "pipeline (clean-up + vision)", done.text, truth, s)
        row["conf"] = done.confidence
        row["note"] = f"{done.method}, handwritten={done.handwritten}, low_confidence={done.low_confidence}"
        rows.append(row)
    return rows


def _row(doc_id: str, engine: str, text: str, truth: str, seconds: float) -> dict:
    return {
        "doc": doc_id, "engine": engine, "cer": cer(truth, text), "lcer": line_cer(truth, text),
        "key": has_key(doc_id, text),
        "sec": seconds, "text": text, "conf": None, "note": "",
    }


def print_table(rows: list[dict]) -> None:
    print(f"{'doc':8} {'engine':26} {'CER':>6} {'lineCER':>7} {'key value':>10} {'s/page':>7}")
    for r in rows:
        key = f"{'yes' if r['key'] else 'NO':>3} ({KEY_VALUES[r['doc']][:18]})"
        print(f"{r['doc']:8} {r['engine']:26} {r['cer']:6.3f} {r['lcer']:7.3f} {key:>10} {r['sec']:7.1f}")
    print()
    for engine in dict.fromkeys(r["engine"] for r in rows):
        sel = [r for r in rows if r["engine"] == engine]
        print(f"{engine:26} mean CER {sum(r['cer'] for r in sel) / len(sel):.3f}  "
              f"mean lineCER {sum(r['lcer'] for r in sel) / len(sel):.3f}  "
              f"keys {sum(r['key'] for r in sel)}/{len(sel)}  mean {sum(r['sec'] for r in sel) / len(sel):.1f} s/page")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docs", nargs="*", help="document ids to run (default: all image documents)")
    ap.add_argument("--vision", action="store_true", help=f"also run the vision model on {', '.join(VISION_DOCS)}")
    ap.add_argument("--show-text", action="store_true", help="print the recognised text of every row")
    args = ap.parse_args()

    manifest = {e["doc_id"]: e for e in json.loads((DOCS / "manifest.json").read_text(encoding="utf-8"))}
    ids = args.docs or list(KEY_VALUES)
    print(f"tesseract {pytesseract.get_tesseract_version()}  vision model {SETTINGS.vision_model}\n")
    rows = []
    for doc_id in ids:
        rows.extend(evaluate(manifest[doc_id], args.vision))
    print_table(rows)
    for r in rows:
        if r["note"]:
            print(f"{r['doc']:8} {r['engine']}: {r['note']}, page confidence {r['conf']}")
    if args.show_text:
        for r in rows:
            print(f"\n--- {r['doc']} {r['engine']}\n{r['text']}")


if __name__ == "__main__":
    main()
