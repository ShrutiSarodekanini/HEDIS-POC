# 0001 OCR and handwriting engines (US-02 S-37 spike)

Status: accepted for the POC. Date: 2026-10-14.

## Decision

- Printed text (scans, photos, screenshots, fax): Tesseract 5.4 after a numpy/Pillow clean-up, `--psm 6`.
- Handwriting, blurred photos and pages Tesseract reads at under 0.75 confidence: the local vision model
  `medgemma:4b` on Ollama, with the Tesseract text kept as a cross-check.
- Nothing else was installed. `qwen2.5vl:7b` was not pulled, because medgemma:4b transcribed both handwriting
  samples acceptably (see below).

## How it was measured

`python scripts/ocr_eval.py --vision` reruns everything below from `data/seed/documents/manifest.json`
(`ground_truth_text`). Scan PDFs D-202 and D-2101 are rendered with pypdfium2 at 200 dpi. For D-1501 the
ground truth is page 2 (page 1 is a fax cover sheet).

- CER: Levenshtein distance / ground-truth length, lower-cased, whitespace-normalised.
- Line CER: the same per ground-truth line against its best match anywhere in the text. It ignores extra
  lines, which matters for D-1102, a portal screenshot whose menus and buttons are not in the ground truth
  (so its plain CER is above 1.0 for every engine and means nothing).
- Key value: the string a HEDIS rule would need, matched case-insensitively.

Machine: CPU only (no GPU), Windows. Seconds are per page.

## Results

Printed Tesseract, raw (grayscale, psm 6) against the same with clean-up:

| Doc | Kind | Key value | Raw CER | Raw key | Clean CER | Clean key | Raw s | Clean s |
|---|---|---|---|---|---|---|---|---|
| D-301 | photo | bilateral mastectomy | 0.000 | yes | 0.000 | yes | 0.4 | 1.2 |
| D-502 | photo | Pap smear 2021 | 0.000 | yes | 0.000 | yes | 0.8 | 2.2 |
| D-903 | tilted table photo | 131/79 | 0.617 | NO | 0.005 | yes | 1.0 | 1.7 |
| D-1701 | poor light photo | Pregnancy confirmed | 0.071 | yes | 0.000 | yes | 0.3 | 1.1 |
| D-9001 | blurred photo | 126/80 | 0.270 | yes | 0.135 | NO | 0.3 | 1.1 |
| D-601 | handwritten | Total abdominal hysterectomy | 0.086 | yes | 0.155 | yes | 0.4 | 1.6 |
| D-1401 | handwritten | 134/82 | 0.155 | NO | 0.131 | NO | 0.5 | 1.6 |
| D-1102 | PNG screenshot | 125/80 | 1.477* | yes | 1.692* | yes | 0.3 | 1.2 |
| D-1501 p2 | fax TIFF | hemodialysis | 0.172** | yes | 0.172** | yes | 0.4 | 3.1 |
| D-202 | scan PDF | Screening mammogram 2025-11-03 | 0.245 | yes | 0.025 | yes | 0.6 | 3.2 |
| D-2101 | scan PDF | metastatic pancreatic cancer | 0.188 | NO | 0.036 | yes | 0.5 | 3.2 |
| Mean | | | 0.298 | 8/11 | 0.214 | 9/11 | 0.5 | 2.0 |
| Mean line CER | | | 0.088 | | 0.030 | | | |

\* Screenshot chrome, not errors: line CER is 0.000 for both.
\** The fax stamp line ("2026-06-30 09:14 FROM: ...") is real text that is not in the ground truth. Line CER 0.003.

Tesseract alone never gets handwriting right: D-1401 "BP 1324/82" (should be 134/82), "HR ZO" (70), "M-044"
(M-014); D-601 "W-006", "3/4/26" (3/9/26). Clean-up does not fix that, and on D-9001 it makes the
punctuation loss worse ("Visit 99 26", "BP 126 80"), so the key value is lost there.

Vision model (medgemma:4b, temperature 0, seed fixed) on the four pages where it matters, and the full
pipeline (clean-up, then the model when the page needs it):

| Doc | Vision on the original photo: CER / key | s per page | Pipeline: CER / key | Pipeline path | Page confidence |
|---|---|---|---|---|---|
| D-601 | 0.009 / yes | 103 | 0.000 / yes | ocr_handwriting | 0.84 |
| D-1401 | 0.000 / yes | 117 | 0.000 / yes | ocr_handwriting | 0.77 |
| D-9001 | 0.000 / yes | 101 | 0.000 / yes | vision (blur rescue) | 0.76 |
| D-903 | 0.124 / yes | 153 | 0.005 / yes | printed OCR, no model call | 0.94 |

Vision seconds are for a first, uncached request. Ollama reuses its prompt cache for a byte-identical
request, so repeat runs of the same image take 8 to 20 s; that is why the script's own timing column can
look faster on a second run. Plan for about 100 to 150 s per page on this CPU.

Vision on the worst printed page (D-903, a tilted table) is good at the values but drops the table layout
(one cell per line) and is 100 times slower than Tesseract with clean-up (CER 0.124 against 0.005), so
printed pages stay on Tesseract.

Psm check on the cleaned printed pages (D-1501, D-903 and the rest, 8 pages): psm 4, 6 and 11 all keep
8/8 key values; mean line CER 0.019 (psm 4), 0.020 (psm 6), 0.026 (psm 11). Differences are noise, so psm 6
(one block, line by line) stays.

## Settings (all in `services/document_intake/converters/image.py`)

- Tesseract: `--psm 6 --dpi 300 -c preserve_interword_spaces=1`, language `eng`. Words under 40 percent
  confidence that contain no letter or digit are dropped (texture, edges).
- Clean-up order: EXIF orientation, grayscale, paper crop (threshold, per-row span fill, background painted
  paper colour; skipped for flat scans and screenshots), deskew (projection variance over -10 to 10 degrees,
  0.1 degree steps), dust removal (lone ink with nothing within 12 px), dark header bands inverted, median and
  blur only for grainy photos (mean difference from a 3x3 median above 2.0), gamma lift when the median is
  under 90, light flattening, black point at the 0.2 percentile (at most 110), white point 205 (drops faint
  ruled lines), table and margin rules erased (straight runs over 6 percent of the page), mild unsharp mask,
  enlarge towards a 36 px text line (at most 3x, long side at most 3200 px).
- Safety nets: words under 30 px high are re-read at about 38 px (D-1501 read "2025-11-03" as "2025-71-03"
  at its native size); a page under 0.6 confidence is retried after a Tesseract OSD turn of 90, 180 or 270.
- Second opinion goes to the vision model when any of these holds: confidence under 0.55
  (`SETTINGS.handwriting_confidence`), the handwriting check, edge sharpness under 0.3, or confidence under
  0.75 (`SETTINGS.ocr_low_confidence`).
- Handwriting check: confidence under 0.55, or (confidence under 0.85 and at least 10 percent of words under
  0.7, and either blue pen ink or ruled writing paper). Printed pages measured 0.45 to 1.0 on sharpness,
  D-9001 0.22. Blue ink is median blue minus red of the ink pixels, 26 to 29 on the two handwritten samples,
  19 on the D-1102 screenshot, 0 or below on black print.
- Vision call: `POST /api/generate`, image as base64 PNG, `temperature 0`, `top_k 1`, `seed 7`. The model sees
  the cropped, deskewed page before contrast changes. On the samples that beat the heavily processed
  image (the processed one lost the slashes in "9/9/26") and was never worse than the raw photo. Pages with a
  long side over 1800 px are cropped to their inked area so small text fills the model input.
- Confidence of a model transcription: 0.4 on its own, plus 0.5 times the share of model tokens that
  Tesseract also read, so 0.9 at most. Words match at similarity 0.8; anything with a digit must match
  exactly, because a number off by one digit is a different number. Under 0.75 the page is flagged
  `low_confidence`; under 0.55 the file goes to `manual_review`.
- `manual_review` also when a page has no text, or when it was flagged handwritten and the model returned
  nothing (the Tesseract text is kept for the reviewer).

## Known weaknesses

- The model is slow: about 100 to 150 s per uncached page on CPU. Every handwritten or blurred page pays it.
  A GPU, or a smaller image, would change that; not tried.
- A 4B model can misread a digit with full confidence (an earlier run read "70" as "7D" on D-1401 with a
  different crop). The agreement score only shows that Tesseract saw the same tokens. Handwritten
  values (BP, dates) still deserve a human check, and every handwritten page keeps `handwritten=True`.
- Black-ink handwriting on plain paper is only caught when Tesseract confidence falls under 0.55.
  The two samples are blue ink on lined paper, so this rule is tuned to them.
- A blurred printed page can show Tesseract confidence 0.84 while dropping every slash and period. Only the
  sharpness check catches it.
- Model answers carry no word boxes and no table layout.
- Perspective is not corrected, only rotation. D-903 works because Tesseract tolerates the slant; a steeper
  angle may not.
- Tesseract reads "lb" as "Ib" in D-903 and reads the white "Reply" button as "kit"; D-1102 menu text is read as
  page text; the dotted fax stamp on D-1501 is read with low quality.
- Dust that touches a letter is not removed. D-2101 is read correctly now, but heavier dust would not be.
- D-9001 is intentionally blurred. The pipeline reads it correctly through the model; if Ollama is not
  running it ends as a normal conversion with the wrong text at confidence 0.84, which is the weakest case
  in this design.
