"""Image converter: JPG, PNG, TIFF (all frames) and HEIC to pages of text.

Pipeline for one page image (see docs/decisions/0001-ocr-and-handwriting-engines.md for the numbers):

1. Clean-up (numpy and Pillow only): EXIF orientation, grayscale, find the sheet of paper in a photo and
   cut the background away, level a skewed page, remove scanner dust, flatten uneven light, set black
   and white points (this drops faint ruled lines), erase table rules, sharpen a little and enlarge
   small text. Every geometric step keeps an affine matrix, so a word box found on the cleaned image
   maps back onto the stored page image.
2. Printed OCR with Tesseract (psm 6), one text line per detected line, table cells joined with " | ".
   Small words are re-read enlarged; a page that reads badly is re-read after an OSD turn.
3. Second opinion: a page that looks handwritten, is blurred, or reads below SETTINGS.ocr_low_confidence
   goes to a local vision model on Ollama (the cropped, levelled page, not the contrast-stretched one).
   Its transcription replaces the Tesseract text only when it is non-empty. The page confidence then
   comes from how well the two texts agree (see agreement_confidence).

Nothing here raises for bad input: a page that cannot be read comes back empty with low_confidence=True,
and convert() reports manual_review. Text is never invented.

Word boxes are in the pixel frame of Page.image_png (the EXIF-corrected page, shrunk only when its long
side is above MAX_PNG_SIDE). For the seed images that is the original pixel frame. Pages answered by the
vision model have words with offsets but no boxes.
"""

from __future__ import annotations

import base64
import io
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import httpx
import numpy as np
import pillow_heif
import pytesseract
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

from ..config import SETTINGS
from ..models import (
    CONVERTED,
    FAILED,
    MANUAL_REVIEW,
    OCR_HANDWRITING,
    OCR_PRINTED,
    VISION,
    ConversionResult,
    Page,
    Word,
)

pillow_heif.register_heif_opener()
pytesseract.pytesseract.tesseract_cmd = SETTINGS.tesseract_cmd
Image.MAX_IMAGE_PIXELS = 200_000_000
log = logging.getLogger(__name__)

MAX_PNG_SIDE = 1700          # stored page image: long side shrunk to 1600 only above this
PNG_TARGET_SIDE = 1600
MAX_OCR_SIDE = 3200          # working image never exceeds this on the long side
MAX_FRAMES = 200
TARGET_LINE_HEIGHT = 36      # px, ascender to descender; smaller text is enlarged towards this
MIN_LINE_HEIGHT = 28
MAX_UPSCALE = 3.0
MIN_WORD_HEIGHT = 30         # Tesseract box height of a word; below this the page is re-read enlarged
TARGET_WORD_HEIGHT = 38
BLUR_SHARPNESS = 0.3          # below this edge steepness a photo counts as blurred
BLUE_INK_TINT = 22.0          # blue minus red of pen ink; black print is about 0
WHITE_POINT = 205            # normalised grey at or above this becomes white (drops faint rules)
TESSERACT_CONFIG = "--psm 6 --dpi 300 -c preserve_interword_spaces=1"
OSD_TRIGGER_CONFIDENCE = 0.6
MODEL_ONLY_CONFIDENCE = 0.4   # confidence of a model-only transcription before agreement is added
AGREEMENT_SPAN = 0.5    # extra confidence earned by agreeing with Tesseract
VISION_TIMEOUT_S = 300.0       # about 100 s per page on a CPU-only machine
CROP_ABOVE_SIDE = 1800
VISION_PROMPT = (
    "Transcribe all the text in this image exactly as written, line by line, keeping the original line "
    "breaks. Copy numbers, dates and units exactly. Output only the transcribed text. Do not add any "
    "commentary, explanation, title or formatting. If nothing is legible, output nothing."
)


@dataclass
class _Work:
    """A working image plus the matrix that maps its pixels onto the page image."""

    gray: Image.Image
    to_page: np.ndarray = field(default_factory=lambda: np.eye(3))
    table: bool = False          # the page had a ruled grid, so cells are joined with " | "
    ruled: bool = False          # the page is lined writing paper
    view: Image.Image | None = None   # cropped and levelled page before normalising, shown to the vision model


# ---------------------------------------------------------------- geometry helpers

def _affine(work: _Work, out_to_in: np.ndarray, size: tuple[int, int], fill: int) -> _Work:
    """Resample work.gray through a 3x3 output->input matrix; compose the page mapping."""
    gray = work.gray.transform(
        size, Image.Transform.AFFINE, tuple(out_to_in[:2].flatten()), Image.Resampling.BICUBIC, fillcolor=fill
    )
    return _Work(gray, work.to_page @ out_to_in, work.table, work.ruled, work.view)


def _translate(dx: float, dy: float) -> np.ndarray:
    return np.array([[1, 0, dx], [0, 1, dy], [0, 0, 1.0]])


def _background(gray: Image.Image) -> int:
    """Paper tone: the median of the brightest 40 percent of pixels."""
    arr = np.asarray(gray)
    cutoff = np.percentile(arr, 60)
    return int(np.median(arr[arr >= cutoff]))


def _otsu(arr: np.ndarray) -> int:
    hist = np.bincount(arr.ravel(), minlength=256).astype(float)
    total = hist.sum()
    sum_all = float(np.dot(np.arange(256), hist))
    best, best_t, w0, s0 = -1.0, 128, 0.0, 0.0
    for t in range(256):
        w0 += hist[t]
        if w0 == 0 or w0 == total:
            continue
        s0 += t * hist[t]
        m0, m1 = s0 / w0, (sum_all - s0) / (total - w0)
        var = w0 * (total - w0) * (m0 - m1) ** 2
        if var > best:
            best, best_t = var, t
    return best_t


def _box_blur(a: np.ndarray, k: int) -> np.ndarray:
    """Mean filter of an odd window k using cumulative sums (reflect padding)."""
    pad = k // 2
    p = np.pad(a.astype(np.float64), pad, mode="edge")
    c = np.cumsum(np.cumsum(p, axis=0), axis=1)
    c = np.pad(c, ((1, 0), (1, 0)))
    h, w = a.shape
    s = c[k:k + h, k:k + w] - c[:h, k:k + w] - c[k:k + h, :w] + c[:h, :w]
    return s / (k * k)


# ---------------------------------------------------------------- step: find the paper

def _crop_to_paper(work: _Work) -> _Work:
    """Cut a photographed sheet out of its background and paint the leftover background paper-coloured.

    Works on a small copy: threshold bright pixels, smooth, then per row take the span between the first
    and last bright pixel (this fills the dark text inside the sheet). Skipped for flat scans and
    screenshots, where the bright region already touches the image edges.
    """
    gray = work.gray
    w, h = gray.size
    scale = 320 / max(w, h)
    if scale >= 1:
        return work
    sw, sh = max(8, round(w * scale)), max(8, round(h * scale))
    small = np.asarray(gray.resize((sw, sh), Image.Resampling.BILINEAR))
    bright = (_box_blur(small > _otsu(small), 5) > 0.5)
    if bright.mean() > 0.97 or bright.mean() < 0.15:
        return work
    rows = np.flatnonzero(bright.any(axis=1))
    cols = np.flatnonzero(bright.any(axis=0))
    margins = [rows[0] / sh, 1 - (rows[-1] + 1) / sh, cols[0] / sw, 1 - (cols[-1] + 1) / sw]
    if sum(m > 0.015 for m in margins) < 3:
        return work
    span = np.zeros_like(bright)
    for y in np.flatnonzero(bright.any(axis=1)):
        xs = np.flatnonzero(bright[y])
        span[y, xs[0]:xs[-1] + 1] = True
    inset = 2                                                   # drop the shadowed paper edge
    keep = _box_blur(span, 2 * inset + 1) > 0.99
    mask = np.asarray(Image.fromarray((keep * 255).astype(np.uint8)).resize((w, h), Image.Resampling.NEAREST)) > 0
    ys, xs = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    if len(ys) == 0 or len(xs) == 0:
        return work
    arr = np.asarray(gray).copy()
    arr[~mask] = _background(gray)
    x0, y0, x1, y1 = xs[0], ys[0], xs[-1] + 1, ys[-1] + 1
    cropped = Image.fromarray(arr).crop((x0, y0, x1, y1))
    return _Work(cropped, work.to_page @ _translate(x0, y0), work.table, work.ruled, work.view)


# ---------------------------------------------------------------- step: deskew

def _skew_angle(gray: Image.Image) -> float:
    """Skew in degrees (counter-clockwise rotation that levels the text), from -10 to 10."""
    w, h = gray.size
    scale = min(1.0, 700 / max(w, h))
    small = gray.resize((max(8, round(w * scale)), max(8, round(h * scale))), Image.Resampling.BILINEAR)
    arr = np.asarray(small)
    ink = (arr < _otsu(arr)).astype(np.uint8) * 255
    if (ink > 0).sum() < 30:
        return 0.0
    ink_img = Image.fromarray(ink)

    def score(angle: float) -> float:
        rot = np.asarray(ink_img.rotate(angle, Image.Resampling.BILINEAR, expand=False), dtype=np.float64)
        proj = rot.sum(axis=1)
        return float(np.sum(np.diff(proj) ** 2))

    coarse = max(np.arange(-10, 10.01, 1.0), key=score)
    fine = max(np.arange(coarse - 1, coarse + 1.01, 0.1), key=score)
    return float(np.clip(fine, -10, 10)) if abs(fine) >= 0.2 else 0.0


def _rotate(work: _Work, angle: float) -> _Work:
    """Rotate counter-clockwise by angle degrees, enlarging the canvas so nothing is cut off."""
    w, h = work.gray.size
    a = -np.radians(angle)
    ca, sa = np.cos(a), np.sin(a)
    nw = int(np.ceil(abs(w * ca) + abs(h * sa)))
    nh = int(np.ceil(abs(w * sa) + abs(h * ca)))
    cx_in, cy_in, cx_out, cy_out = w / 2, h / 2, nw / 2, nh / 2
    m = np.array([
        [ca, sa, cx_in - ca * cx_out - sa * cy_out],
        [-sa, ca, cy_in + sa * cx_out - ca * cy_out],
        [0, 0, 1.0],
    ])
    return _affine(work, m, (nw, nh), _background(work.gray))


def _rotate_quarter(work: _Work, quarters_ccw: int) -> _Work:
    """Exact 90 degree rotation (counter-clockwise quarters); keeps the page mapping."""
    w, h = work.gray.size
    q = quarters_ccw % 4
    if q == 0:
        return work
    if q == 1:
        m, op = np.array([[0, -1, w - 1], [1, 0, 0], [0, 0, 1.0]]), Image.Transpose.ROTATE_90
    elif q == 2:
        m, op = np.array([[-1, 0, w - 1], [0, -1, h - 1], [0, 0, 1.0]]), Image.Transpose.ROTATE_180
    else:
        m, op = np.array([[0, 1, 0], [-1, 0, h - 1], [0, 0, 1.0]]), Image.Transpose.ROTATE_270
    view = work.view.transpose(op) if work.view is not None else None
    return _Work(work.gray.transpose(op), work.to_page @ m, work.table, work.ruled, view)


# ---------------------------------------------------------------- step: rules, light, scale

def _long_runs(binary: np.ndarray, min_len: int) -> np.ndarray:
    """Mask of pixels that belong to a horizontal run of True at least min_len long."""
    out = np.zeros_like(binary)
    for y in np.flatnonzero(binary.sum(axis=1) >= min_len):
        row = np.concatenate(([False], binary[y], [False]))
        edges = np.flatnonzero(row[1:] != row[:-1])
        for s, e in zip(edges[::2], edges[1::2]):
            if e - s >= min_len:
                out[y, s:e] = True
    return out


def _bands(profile: np.ndarray, threshold: float) -> int:
    """Number of separate runs of a profile above a threshold."""
    on = (profile > threshold).astype(int)
    return int((np.diff(np.concatenate(([0], on))) == 1).sum())


def _remove_rules(work: _Work) -> _Work:
    """Erase long horizontal and vertical lines (table grids, ruled paper, margin lines).

    Expects a flattened image (paper near 255). A pixel is erased only when it is part of a straight
    run of at least 6 percent of the image side, so letters are untouched. A page with at least two
    horizontal and two vertical long lines is treated as a table.
    """
    arr = np.asarray(work.gray).copy()
    h, w = arr.shape
    ink = arr < 150
    rules_h = _long_runs(ink, max(40, int(w * 0.06)))
    rules_v = _long_runs(ink.T, max(40, int(h * 0.06))).T
    if not (rules_h.any() or rules_v.any()):
        return work
    smooth_v = _box_blur(rules_v, 15).sum(axis=0) * 15 * 15 / 15
    table = _bands(rules_h.sum(axis=1), 0.3 * w) >= 3 and _bands(smooth_v, 0.3 * h) >= 3
    rules = rules_h | rules_v
    grown = np.asarray(Image.fromarray((rules * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))) > 0
    arr[grown] = 255
    return _Work(Image.fromarray(arr), work.to_page, table, work.ruled, work.view)


def _flatten_light(gray: Image.Image) -> Image.Image:
    """Divide out the slowly varying paper brightness (shadows, vignetting, poor light)."""
    w, h = gray.size
    f = 4
    small = gray.resize((max(8, w // f), max(8, h // f)), Image.Resampling.BILINEAR)
    small = small.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(6))
    bg = np.asarray(small.resize((w, h), Image.Resampling.BILINEAR), dtype=np.float32)
    out = np.clip(np.asarray(gray, dtype=np.float32) / np.maximum(bg, 1.0) * 255.0, 0, 255)
    return Image.fromarray(out.astype(np.uint8))


def _gamma_lift(gray: Image.Image) -> Image.Image:
    """Brighten a very dark image: gamma chosen so the median lands near mid-grey."""
    arr = np.asarray(gray, dtype=np.float32) / 255.0
    med = float(np.clip(np.median(arr), 0.02, 0.98))
    gamma = float(np.clip(np.log(0.6) / np.log(med), 0.3, 1.0))
    return Image.fromarray((np.power(arr, gamma) * 255).astype(np.uint8))


def _line_height(gray: Image.Image) -> float | None:
    """Median height of the text-line bands (ascender to descender), or None when there is no text."""
    arr = np.asarray(gray)
    ink = arr < _otsu(arr)
    prof = ink.sum(axis=1).astype(np.float64)
    if prof.max() < 3:
        return None
    band = prof > max(2.0, 0.08 * prof.max())
    edges = np.flatnonzero(np.diff(np.concatenate(([0], band.astype(int), [0]))))
    heights = [e - s for s, e in zip(edges[::2], edges[1::2]) if e - s >= 6]
    return float(np.median(heights)) if heights else None


def _noise_level(gray: Image.Image) -> float:
    """Mean absolute difference from a 3x3 median: about 0.7 for a clean photo, above 3 for heavy grain."""
    a = np.asarray(gray, dtype=np.float32)
    m = np.asarray(gray.filter(ImageFilter.MedianFilter(3)), dtype=np.float32)
    return float(np.abs(a - m).mean())


def _invert_dark_bands(gray: Image.Image) -> Image.Image:
    """Flip full-width dark bands (white text on a coloured header) so Tesseract sees dark text."""
    arr = np.asarray(gray)
    h = arr.shape[0]
    if h < 60 or float(np.median(arr)) < 140:
        return gray
    dark = arr.mean(axis=1) < 110
    edges = np.flatnonzero(np.diff(np.concatenate(([0], dark.astype(int), [0]))))
    out = arr.copy()
    for s, e in zip(edges[::2], edges[1::2]):
        if 18 <= e - s <= 0.4 * h:
            out[s:e] = 255 - out[s:e]
    return Image.fromarray(out)


def _is_ruled(flat: np.ndarray) -> bool:
    """Lined writing paper: at least six faint horizontal lines spanning the page."""
    w = flat.shape[1]
    faint = (flat >= 150) & (flat <= 235)
    lines = _long_runs(faint, int(w * 0.4))
    return _bands(lines.sum(axis=1), 0.4 * w) >= 6


def _remove_specks(gray: Image.Image) -> Image.Image:
    """Erase lone dark specks (scanner dust): ink with no other ink within 12 px.

    Letters, digits and punctuation inside text always have neighbours that close, so only dust is lost.
    """
    arr = np.asarray(gray)
    ink = arr < min(128, _background(gray) - 60)
    if not ink.any():
        return gray
    near = _box_blur(ink, 11) * 121
    far = _box_blur(ink, 25) * 625
    lone = ink & ((far - near) < 0.5)
    if lone.sum() < 20:
        return gray
    grown = np.asarray(Image.fromarray((lone * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(9))) > 0
    out = arr.copy()
    out[grown] = _background(gray)
    return Image.fromarray(out)


def _normalise(gray: Image.Image) -> tuple[Image.Image, bool]:
    """Denoise grainy photos, lift dark ones, flatten uneven light and set black and white points.

    Returns the normalised image and whether the paper is ruled.
    """
    gray = _remove_specks(gray)
    if _noise_level(gray) > 2.0:
        gray = gray.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
    if float(np.median(np.asarray(gray))) < 90:
        gray = _gamma_lift(gray)
    gray = _flatten_light(_invert_dark_bands(gray))
    arr = np.asarray(gray, dtype=np.float32)
    ruled = _is_ruled(arr)
    black = min(float(np.percentile(arr, 0.2)), 110.0)
    out = np.clip((arr - black) / max(WHITE_POINT - black, 1.0), 0, 1) * 255.0
    return Image.fromarray(out.astype(np.uint8)), ruled


def _resize(work: _Work, factor: float) -> _Work:
    """Scale the working image by factor, keeping the mapping to page pixels."""
    gray = work.gray
    size = (max(1, round(gray.width * factor)), max(1, round(gray.height * factor)))
    resized = gray.resize(size, Image.Resampling.LANCZOS if factor > 1 else Image.Resampling.AREA)
    m = np.diag([size[0] / gray.width, size[1] / gray.height, 1.0])
    return _Work(resized, work.to_page @ np.linalg.inv(m), work.table, work.ruled, work.view)


def _fit_factor(work: _Work, wanted: float) -> float:
    """Clamp a scale factor so the long side stays within MAX_OCR_SIDE."""
    return min(wanted, MAX_OCR_SIDE / max(work.gray.size))


def _enlarge(work: _Work) -> _Work:
    """Sharpen a little, then enlarge small text towards TARGET_LINE_HEIGHT (a first guess; see _read)."""
    work = _Work(work.gray.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=2)),
                 work.to_page, work.table, work.ruled, work.view)
    lh = _line_height(work.gray)
    wanted = min(MAX_UPSCALE, TARGET_LINE_HEIGHT / lh) if lh and lh < MIN_LINE_HEIGHT else 1.0
    factor = _fit_factor(work, wanted)
    return work if abs(factor - 1) < 0.05 else _resize(work, factor)


def _page_image(image: Image.Image) -> Image.Image:
    """The page as stored: EXIF-corrected RGB, shrunk only when very large."""
    img = ImageOps.exif_transpose(image) or image
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        base = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        img = Image.alpha_composite(base, rgba)
    if img.mode in ("I;16", "I", "F"):
        arr = np.asarray(img, dtype=np.float64)
        span = max(float(arr.max() - arr.min()), 1.0)
        img = Image.fromarray(((arr - arr.min()) / span * 255).astype(np.uint8))
    return img.convert("RGB")


def prepare(image: Image.Image) -> tuple[Image.Image, _Work]:
    """Return (page image, cleaned working image with its mapping). Page image is RGB at store size."""
    page = _page_image(image)
    scale = 1.0
    if max(page.size) > MAX_PNG_SIDE:
        scale = PNG_TARGET_SIDE / max(page.size)
    start = _Work(page.convert("L"), np.diag([scale, scale, 1.0]))
    work = _crop_to_paper(start)
    angle = _skew_angle(work.gray)
    if angle:
        work = _rotate(work, angle)
    flat, ruled = _normalise(work.gray)
    work = _remove_rules(_Work(flat, work.to_page, False, ruled, work.gray))
    return page, _enlarge(work)


# ---------------------------------------------------------------- Tesseract

@dataclass
class _Token:
    text: str
    box: tuple[int, int, int, int]   # x, y, w, h on the working image
    conf: float                      # 0..1
    line: tuple[int, int, int]


def _recognise(gray: Image.Image) -> list[_Token]:
    """Run Tesseract and return word tokens in reading order; noise is dropped."""
    data = pytesseract.image_to_data(
        gray, lang="eng", config=TESSERACT_CONFIG, output_type=pytesseract.Output.DICT, timeout=90
    )
    tokens: list[_Token] = []
    for i, raw in enumerate(data["text"]):
        text = str(raw).strip()
        conf = float(data["conf"][i])
        if not text or conf < 0 or int(data["level"][i]) != 5:
            continue
        if conf < 40 and not re.search(r"[A-Za-z0-9]", text):
            continue                                            # stray marks from texture or edges
        tokens.append(_Token(
            text, (int(data["left"][i]), int(data["top"][i]), int(data["width"][i]), int(data["height"][i])),
            conf / 100.0, (int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i])),
        ))
    return tokens


def _page_confidence(tokens: list[_Token]) -> float:
    """Mean word confidence weighted by word length; 0.0 when nothing was read."""
    weight = sum(len(t.text) for t in tokens)
    return sum(t.conf * len(t.text) for t in tokens) / weight if weight else 0.0


def _layout(tokens: list[_Token], work: _Work, page_size: tuple[int, int]) -> tuple[str, list[Word]]:
    """Join tokens into lines of text and build Words with offsets and boxes in page pixels."""
    med = float(np.median([t.box[3] for t in tokens])) if tokens else 0.0
    # a bar taller than the text is the edge of a table or page border, not a letter
    tokens = [t for t in tokens if not (re.fullmatch(r"[|!lI]{1,2}", t.text) and t.box[3] > 1.8 * med)]
    lines: dict[tuple[int, int, int], list[_Token]] = {}
    for t in tokens:
        lines.setdefault(t.line, []).append(t)
    heights = [t.box[3] for t in tokens]
    med_h = float(np.median(heights)) if heights else 0.0
    text, words = "", []
    for key in sorted(lines, key=lambda k: (min(t.box[1] for t in lines[k]), k)):
        row = sorted(lines[key], key=lambda t: t.box[0])
        if text:
            text += "\n"
        prev_end = None
        for t in row:
            if prev_end is not None:
                gap = t.box[0] - prev_end
                text += " | " if work.table and gap > 1.2 * med_h else ("  " if gap > 1.5 * med_h else " ")
            start = len(text)
            text += t.text
            words.append(_word(t, start, work.to_page, page_size))
            prev_end = t.box[0] + t.box[2]
    return text, words


def _word(t: _Token, start: int, to_page: np.ndarray, page_size: tuple[int, int]) -> Word:
    x, y, w, h = t.box
    corners = np.array([[x, y, 1], [x + w, y, 1], [x, y + h, 1], [x + w, y + h, 1]], dtype=float) @ to_page.T
    px0, py0 = max(0.0, float(corners[:, 0].min())), max(0.0, float(corners[:, 1].min()))
    px1 = min(float(page_size[0]), float(corners[:, 0].max()))
    py1 = min(float(page_size[1]), float(corners[:, 1].max()))
    return Word(
        t.text, start, start + len(t.text), round(px0, 1), round(py0, 1),
        round(max(px1 - px0, 0.0), 1), round(max(py1 - py0, 0.0), 1), round(t.conf, 3),
    )


def _osd_quarters(gray: Image.Image) -> int:
    """Counter-clockwise quarter turns Tesseract OSD says would make the page upright, else 0."""
    try:
        osd = pytesseract.image_to_osd(gray, config="--psm 0", output_type=pytesseract.Output.DICT, timeout=30)
    except Exception as exc:                                    # OSD fails on sparse pages; that is normal
        log.debug("OSD unavailable: %s", exc)
        return 0
    if float(osd.get("orientation_conf", 0)) < 2.0:
        return 0
    return ((360 - int(osd.get("rotate", 0))) // 90) % 4     # OSD reports clockwise degrees


# ---------------------------------------------------------------- handwriting

def _ink_fraction(gray: Image.Image) -> float:
    arr = np.asarray(gray)
    return float((arr < min(_otsu(arr), 150)).mean())


def _content_crop(gray: Image.Image) -> Image.Image:
    """Crop to the inked area plus a margin, so the vision model sees the writing as large as possible."""
    arr = np.asarray(gray)
    ink = arr < min(_otsu(arr), 170)
    ys, xs = np.flatnonzero(ink.sum(axis=1) >= 2), np.flatnonzero(ink.sum(axis=0) >= 2)
    if len(ys) == 0 or len(xs) == 0:
        return gray
    m = int(0.03 * max(gray.size)) + 10
    box = (max(0, xs[0] - m), max(0, ys[0] - m), min(gray.width, xs[-1] + m), min(gray.height, ys[-1] + m))
    return gray.crop(box)


def transcribe_with_vision(image: Image.Image, *, crop: bool | None = None) -> str | None:
    """Ask the local vision model for a line-by-line transcription. None on any failure or empty answer.

    crop=True cuts the image to its inked area first so the writing fills more of the model's input.
    The default (None) crops only large pages (long side above CROP_ABOVE_SIDE), where small text on a
    mostly blank sheet would otherwise be unreadable at the model's input size; on the photo samples
    cropping made the model slightly worse (it read "70" as "7D").
    """
    img = _content_crop(image) if (max(image.size) > CROP_ABOVE_SIDE if crop is None else crop) else image
    if max(img.size) > 1400:
        img = img.resize((round(img.width * 1400 / max(img.size)), round(img.height * 1400 / max(img.size))),
                         Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    body = {
        "model": SETTINGS.vision_model,
        "prompt": VISION_PROMPT,
        "images": [base64.b64encode(buf.getvalue()).decode("ascii")],
        "stream": False,
        "options": {"temperature": 0, "seed": 7, "top_k": 1, "num_predict": 600},
    }
    try:
        resp = httpx.post(f"{SETTINGS.ollama_url}/api/generate", json=body, timeout=VISION_TIMEOUT_S)
        resp.raise_for_status()
        raw = str(resp.json().get("response", ""))
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("vision model unavailable: %s", exc)
        return None
    return _clean_model_text(raw) or None


def _clean_model_text(raw: str) -> str:
    """Strip code fences and blank lines from the model answer."""
    lines = [ln.rstrip() for ln in raw.strip().splitlines() if not ln.strip().startswith("```")]
    return "\n".join(ln for ln in lines if ln.strip()).strip()


def _norm_tokens(text: str) -> list[str]:
    return [t for t in re.sub(r"[^\w/\-]+", " ", text.lower()).split() if t]


def agreement(tess_text: str, model_text: str) -> float:
    """Share of the model's tokens that Tesseract also read, each Tesseract token used once.

    Words match at similarity >= 0.8; anything containing a digit must match exactly.

    1.0 means every model token has a matching Tesseract token; 0.0 means none do or Tesseract read nothing.
    """
    model, tess = _norm_tokens(model_text), _norm_tokens(tess_text)
    if not model or not tess:
        return 0.0
    free, hits = list(tess), 0
    for tok in model:
        best_i, best = -1, 0.0
        for i, cand in enumerate(free):
            if cand == tok:
                r = 1.0
            elif any(c.isdigit() for c in tok + cand):
                r = 0.0                                         # a number off by one digit is another number
            else:
                r = SequenceMatcher(None, cand, tok).ratio()
            if r > best:
                best_i, best = i, r
        if best >= 0.8:
            hits += 1
            free.pop(best_i)
    return hits / len(model)


def agreement_confidence(tess_text: str, model_text: str) -> float:
    """Confidence of a model transcription: 0.4 on its own, up to 0.9 when Tesseract agrees on every token."""
    return round(MODEL_ONLY_CONFIDENCE + AGREEMENT_SPAN * agreement(tess_text, model_text), 3)


def _word_boxes(words: list[Word], page_gray: np.ndarray, min_conf: float = 0.0) -> list[tuple[int, int, int, int]]:
    """Integer boxes (x0, y0, x1, y1) of confident words that are big enough to measure."""
    h, w = page_gray.shape
    boxes = []
    for wd in words:
        if wd.x is None or (wd.confidence or 0.0) < min_conf:
            continue
        x0, y0 = int(wd.x), int(wd.y)
        x1, y1 = min(w, int(wd.x + wd.width)), min(h, int(wd.y + wd.height))
        if x1 - x0 >= 8 and y1 - y0 >= 8:
            boxes.append((x0, y0, x1, y1))
    return boxes


def ink_tint(page: Image.Image, words: list[Word]) -> float:
    """Median blue-minus-red value of ink pixels in dark-on-light word boxes (about 0 for black print).

    Pen ink is usually blue, so a clearly positive value is evidence of handwriting. Boxes on a dark
    background (white text on a header) are skipped.
    """
    rgb = np.asarray(page).astype(np.int32)
    gray = rgb.mean(axis=2)
    vals = []
    for x0, y0, x1, y1 in _word_boxes(words, gray, 0.3):
        g = gray[y0:y1, x0:x1]
        if np.median(g) < 150:
            continue
        ink = g < np.median(g) - 60
        if ink.sum() >= 8:
            px = rgb[y0:y1, x0:x1][ink]
            vals.append(float(np.median(px[:, 2] - px[:, 0])))
    return float(np.median(vals)) if vals else 0.0


def sharpness(page: Image.Image, words: list[Word]) -> float | None:
    """Edge steepness of the text: 98th percentile gradient over contrast, median across words.

    About 0.45 to 1.0 for a sharp page and about 0.2 for a blurred one; None if nothing measurable.
    """
    g = np.asarray(page.convert("L")).astype(np.float32)
    grad = np.maximum(np.abs(np.diff(g, axis=1))[:-1], np.abs(np.diff(g, axis=0))[:, :-1])
    vals = []
    for x0, y0, x1, y1 in _word_boxes(words, g):
        sub = g[y0:y1, x0:x1]
        contrast = np.percentile(sub, 98) - np.percentile(sub, 2)
        if contrast >= 30:
            vals.append(float(np.percentile(grad[y0:y1 - 1, x0:x1 - 1], 98) / contrast))
    return float(np.median(vals)) if vals else None


def looks_handwritten(work: _Work, tokens: list[_Token], confidence: float, tint: float) -> bool:
    """Handwriting check.

    Yes when Tesseract is lost (confidence below SETTINGS.handwriting_confidence), or when it is
    struggling (confidence under 0.85 with at least 10 percent of words under 0.7) and the page also
    has handwriting evidence: blue pen ink or ruled writing paper.
    """
    if confidence < SETTINGS.handwriting_confidence:
        return True
    if not tokens or confidence >= 0.85:
        return False
    shaky = sum(t.conf < 0.7 for t in tokens) / len(tokens) >= 0.10
    return shaky and (tint >= BLUE_INK_TINT or work.ruled)


# ---------------------------------------------------------------- public API

def _store_size(size: tuple[int, int]) -> tuple[int, int]:
    """Size of the stored page image: unchanged unless the long side is above MAX_PNG_SIDE."""
    if max(size) <= MAX_PNG_SIDE:
        return size
    f = PNG_TARGET_SIDE / max(size)
    return round(size[0] * f), round(size[1] * f)


def _png(image: Image.Image) -> bytes:
    img = image
    if _store_size(img.size) != img.size:
        img = img.resize(_store_size(img.size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False)
    return buf.getvalue()


def _tesseract_version() -> str:
    try:
        return f"tesseract {str(pytesseract.get_tesseract_version()).split('.20')[0]}"
    except Exception:
        return "tesseract"


def ocr_image(image: Image.Image, page_no: int, *, use_vision: bool = True) -> Page:
    """OCR one page image into a Page. Never raises; an unreadable page is empty with low_confidence=True.

    use_vision=False keeps the result to Tesseract only (used by the engine comparison script).
    """
    engine = _tesseract_version()
    try:
        page_img, work = prepare(image)
        png = _png(page_img)
        psize = _store_size(page_img.size)
    except Exception as exc:
        log.warning("page %s could not be prepared: %s", page_no, exc)
        return Page(page_no, "", OCR_PRINTED, 0.0, [], None, False, True, engine)
    try:
        tokens, work = _read(work)
    except Exception as exc:
        log.warning("tesseract failed on page %s: %s", page_no, exc)
        tokens = []
    conf = _page_confidence(tokens)
    text, words = _layout(tokens, work, psize)
    page = Page(page_no, text, OCR_PRINTED, round(conf, 3) if tokens else 0.0, words, png, False,
                conf < SETTINGS.ocr_low_confidence, engine)
    if not (use_vision and _ink_fraction(work.gray) > 0.0005):
        return page
    try:
        return _second_opinion(page, work, page_img, psize, tokens, conf, text)
    except Exception as exc:                                    # a failed extra check never loses the OCR text
        log.warning("vision step failed on page %s: %s", page_no, exc)
        return page


def _second_opinion(page: Page, work: _Work, page_img: Image.Image, psize: tuple[int, int],
                    tokens: list[_Token], conf: float, tess_text: str) -> Page:
    """Send handwriting, blurred or low-confidence pages to the vision model and merge its answer."""
    shown = page_img if psize == page_img.size else page_img.resize(psize)
    sharp = sharpness(shown, page.words)
    page.handwritten = looks_handwritten(work, tokens, conf, ink_tint(shown, page.words))
    blurred = sharp is not None and sharp < BLUR_SHARPNESS
    if not (page.handwritten or blurred or page.low_confidence):
        return page
    model_text = transcribe_with_vision(work.view if work.view is not None else work.gray)
    if not model_text:
        return page
    page.method = OCR_HANDWRITING if page.handwritten else VISION
    page.text, page.words = model_text, _tokenise(model_text)
    page.confidence = agreement_confidence(tess_text, model_text)
    page.low_confidence = page.confidence < SETTINGS.ocr_low_confidence
    page.ocr_engine = f"{_tesseract_version()} + ollama:{SETTINGS.vision_model}"
    return page


def _read(work: _Work) -> tuple[list[_Token], _Work]:
    """Printed OCR with two safety nets: retry larger when the words came out small, and when the
    page reads poorly ask Tesseract OSD whether it is turned and retry upright."""
    tokens = _recognise(work.gray)
    heights = [t.box[3] for t in tokens if len(t.text) > 2]
    if heights and float(np.median(heights)) < MIN_WORD_HEIGHT:
        factor = _fit_factor(work, min(MAX_UPSCALE, TARGET_WORD_HEIGHT / float(np.median(heights))))
        if factor > 1.05:
            bigger = _resize(work, factor)
            again = _recognise(bigger.gray)
            if _page_confidence(again) >= _page_confidence(tokens):
                tokens, work = again, bigger
    conf = _page_confidence(tokens)
    if conf < OSD_TRIGGER_CONFIDENCE and _ink_fraction(work.gray) > 0.002:
        quarters = _osd_quarters(work.gray)
        if quarters:
            turned = _rotate_quarter(work, quarters)
            again = _recognise(turned.gray)
            if _page_confidence(again) > conf:
                return again, turned
    return tokens, work


def _tokenise(text: str) -> list[Word]:
    """Words for a model transcription: offsets only, no boxes and no per-word confidence."""
    return [Word(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", text)]


def _frames(image: Image.Image) -> list[Image.Image]:
    """All frames of a multi-page image (TIFF), each fully loaded."""
    count = min(getattr(image, "n_frames", 1), MAX_FRAMES)
    frames = []
    for i in range(count):
        image.seek(i)
        frames.append(image.copy())
    return frames


def convert(data: bytes, filename: str) -> ConversionResult:
    """Convert image bytes to pages. manual_review when any page could not be read; failed if not an image."""
    try:
        image = Image.open(io.BytesIO(data))
        frames = _frames(image)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError, Image.DecompressionBombError) as exc:
        return ConversionResult([], FAILED, f"{filename}: not a readable image ({exc})")
    pages = [ocr_image(frame, i) for i, frame in enumerate(frames, start=1)]
    floor = SETTINGS.handwriting_confidence
    # unreadable: no text, little agreement, or handwriting that the vision model could not transcribe
    bad = [p.page_no for p in pages if not p.text.strip() or (p.confidence or 0.0) < floor
           or (p.handwritten and p.method != OCR_HANDWRITING)]
    if bad:
        listed = ", ".join(str(n) for n in bad)
        return ConversionResult(pages, MANUAL_REVIEW, f"page(s) {listed} could not be read reliably; read the original")
    return ConversionResult(pages, CONVERTED)
