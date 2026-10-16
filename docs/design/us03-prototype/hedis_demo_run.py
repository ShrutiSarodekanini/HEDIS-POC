"""REFERENCE ONLY, not production code. The US-03 prototype used for the six-gap model comparison (2026-10).

Its DOCS and GAPS are made-up demo data, not the seed data. Reuse its ideas (system prompt, provider quirks,
quote narrowing, KED pairing), not its structure.

HEDIS evidence extraction demo on a local Ollama model.

Runs six seeded gaps through the model, validates every finding, and logs each step.
Usage:
  python hedis_demo_run.py qwen3:8b                                   (local Ollama)
  python hedis_demo_run.py openai/gpt-oss-120b --provider groq          (needs GROQ_API_KEY)
  python hedis_demo_run.py nvidia/nemotron-3-super-120b-a12b:free --provider openrouter  (needs OPENROUTER_API_KEY)
"""

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/chat"
HOSTED = {
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY"),
}
_args = argparse.ArgumentParser()
_args.add_argument("model", nargs="?", default="qwen3:8b")
_args.add_argument("--provider", choices=["ollama", *HOSTED], default="ollama")
ARGS = _args.parse_args()
MODEL, PROVIDER = ARGS.model, ARGS.provider
MY = 2026
YEAR_END = date(MY, 12, 31)
LOG_PATH = Path(__file__).with_name(f"demo_run_log_{PROVIDER}_{re.sub(r'[^A-Za-z0-9.-]+', '_', MODEL)}.jsonl")

os.system("")  # turns on ANSI colours in the Windows console
DIM, RESET, BOLD = "\033[2m", "\033[0m", "\033[1m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"


def log(step: str, msg: str, colour: str = "") -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{DIM}{ts}{RESET} {colour}{step:<9}{RESET} {msg}", flush=True)


# ---------------------------------------------------------------- seed data

DOCS = {
    "D-101": {"member": "M-123", "type": "clinic note", "date": "2026-04-10", "source": "Riverside Family Clinic",
              "text": "Progress note. 58 y/o male here for annual visit. History: GERD, hyperlipidemia. "
                      "Colonoscopy 2019, Dr. Xu, no polyps. Family history: father had colon cancer, colonoscopy 2020. "
                      "Plan: discussed repeat colonoscopy in 2029. Continue statin."},
    "D-102": {"member": "M-123", "type": "HIE record", "date": "2026-06-01", "source": "State HIE",
              "text": "Referral summary. Colonoscopy recommended; patient to schedule with GI."},
    "D-210": {"member": "M-456", "type": "clinic note", "date": "2026-09-14", "source": "Lakeside Internal Medicine",
              "text": "Follow-up for hypertension. Vitals: BP 132/78, HR 72. Tolerating lisinopril 20 mg. Continue current plan."},
    "D-211": {"member": "M-456", "type": "portal upload", "date": "2026-10-01", "source": "Member portal",
              "text": "Home BP log: patient reports readings around 125/80 most mornings."},
    "D-212": {"member": "M-456", "type": "ED note", "date": "2026-08-01", "source": "County Hospital ED",
              "text": "Seen in the emergency department for ankle sprain. Triage BP 128/80. Discharged home."},
    "D-301": {"member": "M-789", "type": "lab report", "date": "2026-08-20", "source": "Quest lab feed (PDF)",
              "text": "Hemoglobin A1c: 7.6 % (collected 2026-08-20). Reference range 4.0-5.6 %."},
    "D-302": {"member": "M-789", "type": "clinic note", "date": "2026-09-05", "source": "Northside Endocrinology",
              "text": "A1c ordered today. Patient says her last A1c was around 7."},
    "D-303": {"member": "M-789", "type": "clinic note", "date": "2026-09-05", "source": "Northside Endocrinology",
              "text": "Functional status: uses a walker, two falls this year. Advanced Alzheimer dementia, on donepezil."},
    "D-401": {"member": "M-555", "type": "lab report", "date": "2026-05-10", "source": "LabCorp",
              "text": "eGFR 68 mL/min/1.73m2 (CKD-EPI). Urine albumin 12 mg/L."},
    "D-402": {"member": "M-555", "type": "lab report", "date": "2026-05-16", "source": "LabCorp",
              "text": "Urine creatinine 110 mg/dL."},
    "D-501": {"member": "M-610", "type": "clinic note", "date": "2026-02-02", "source": "Women's Health Associates",
              "text": "Surgical history: supracervical hysterectomy 2015 for fibroids, cervix retained. "
                      "Pap smear 03/2024, NILM. Patient also reports a Pap at another clinic years ago."},
    "D-601": {"member": "M-720", "type": "clinic note", "date": "2026-04-12", "source": "Lakeside Internal Medicine",
              "text": "Stopped atorvastatin in 2025 due to severe myalgia. Discussed rosuvastatin; patient declined. "
                      "Med list: atorvastatin 40 mg (discontinued)."},
}

GAPS = [
    {"gap_id": "G-0001", "member": "M-123", "age": 58, "sex": "male", "measure": "COL-E",
     "name": "Colorectal Cancer Screening", "missing": "No colorectal cancer screening on record",
     "last_event": None,
     "expect": {"find": ["Colonoscopy 2019"], "avoid": ["father", "2029", "recommended"]}},
    {"gap_id": "G-0002", "member": "M-456", "age": 63, "sex": "male", "measure": "CBP",
     "name": "Controlling High Blood Pressure", "missing": "Most recent BP in 2026 is not under 140/90",
     "last_event": {"date": "2026-07-02", "value": "146/88"},
     "expect": {"find": ["132/78"], "avoid": ["125/80", "128/80"]}},
    {"gap_id": "G-0003", "member": "M-789", "age": 71, "sex": "female", "measure": "GSD",
     "name": "Glycemic Status Assessment for Patients With Diabetes",
     "missing": "Most recent HbA1c or GMI in 2026 is not under 8.0%",
     "last_event": {"date": "2026-03-11", "value": "9.2%"}, "exclusion_check": True,
     "expect": {"find": ["7.6"], "avoid": ["around 7"]}},
    {"gap_id": "G-0004", "member": "M-555", "age": 66, "sex": "male", "measure": "KED",
     "name": "Kidney Health Evaluation for Patients With Diabetes",
     "missing": "No eGFR and uACR pair on record in 2026", "last_event": None,
     "expect": {"find": ["eGFR 68"], "avoid": [], "status": "Pending review (1 of 2 KED tests found)"}},
    {"gap_id": "G-0005", "member": "M-610", "age": 52, "sex": "female", "measure": "CCS-E",
     "name": "Cervical Cancer Screening", "missing": "No cervical cytology or HPV test on record",
     "last_event": None,
     "expect": {"find": ["Pap smear 03/2024"], "avoid": ["supracervical", "years ago"]}},
    {"gap_id": "G-0006", "member": "M-720", "age": 62, "sex": "male", "measure": "SPD",
     "name": "Statin Therapy for Patients With Diabetes",
     "missing": "No statin dispensed in 2026 (pharmacy claims). Look only for exclusions.", "last_event": None,
     "expect": {"find": ["myalgia"], "avoid": ["declined"]}},
]


def rule_text(gap: dict) -> str:
    last = gap["last_event"]["date"] if gap["last_event"] else None
    rules = {
        "COL-E": ("Counts: colonoscopy 2017 to 2026; CT colonography or flexible sigmoidoscopy 2022 to 2026; "
                  "stool DNA with FIT (Cologuard) 2024 to 2026; FOBT or FIT in 2026.",
                  "Exclusions: colorectal cancer or total colectomy at any time; hospice or palliative care in 2026."),
        "CBP": (f"Counts: a blood pressure reading dated after {last} and on or before 2026-12-31 with systolic "
                "under 140 AND diastolic under 90. Readings in the emergency department or during a hospital stay "
                "do not count. Undated readings and readings without both numbers do not count. A home reading "
                "counts only when it has its own exact numbers and its own date; a summary such as 'readings around "
                "125/80' does not count.",
                "Exclusions: ESRD, dialysis, kidney transplant, pregnancy in 2026, hospice or palliative care."),
        "GSD": (f"Counts: an HbA1c or GMI result dated after {last} and on or before 2026-12-31 that is under 8.0%. "
                "It must have a numeric value and a date. Ordered tests and approximate values do not count.",
                "Exclusions (member is 66 or older): frailty AND advanced illness such as dementia; "
                "hospice or palliative care."),
        "KED": ("Counts: BOTH an eGFR result in 2026 AND a uACR in 2026. A uACR is a reported albumin-to-creatinine "
                "ratio in mg/g, or a quantitative urine albumin and a urine creatinine taken no more than 4 days apart. "
                "Report each test as its own finding with evidence_type 'eGFR', 'uACR' (only for a ratio in mg/g), "
                "'urine albumin' or 'urine creatinine'. Dipstick results do not count.",
                "Exclusions: ESRD or dialysis at any time; hospice or palliative care."),
        "CCS-E": ("Counts: cervical cytology (Pap) with a result 2024 to 2026; high-risk HPV test with a result "
                  "2022 to 2026 if the member was 30 or older. Patient-reported tests without a date or result "
                  "do not count.",
                  "Exclusions: hysterectomy with no remaining cervix (total or radical) at any time. A supracervical "
                  "or partial hysterectomy is NOT an exclusion because the cervix remains."),
        "SPD": ("Numerator comes from pharmacy fills, not notes. Do not return numerator evidence.",
                "Exclusions: muscle pain, myopathy or rhabdomyolysis caused by a statin at any time; pregnancy, "
                "IVF, ESRD, dialysis or cirrhosis in 2025 or 2026. Declining a statin is NOT an exclusion."),
    }
    counts, excl = rules[gap["measure"]]
    return f"{counts}\n{excl}"


SYSTEM = """You find evidence in clinical documents for a HEDIS care gap. A trained person reviews everything you return.
Rules:
- Return only evidence about this patient. Ignore family history.
- Only completed care counts. Ignore planned, recommended, ordered, due or declined care.
- Approximate measurements do not count: ignore readings or results described as 'around', 'about' or 'usually', and summaries of several readings. This is about measured values only. A year alone is a valid date for a past procedure or test, for example 'Colonoscopy 2019'.
- "excerpt" must be copied word for word from the document: only the single sentence that contains the evidence, never several sentences.
- "service_date" is when the care happened, as YYYY, YYYY-MM or YYYY-MM-DD. Use the date in the excerpt; if the excerpt has no date, use the document date.
- "evidence_kind" is "numerator" for care that closes the gap, or "exclusion" for a reason to remove the member from the measure.
- If nothing qualifies, return an empty findings list."""

SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "excerpt": {"type": "string"},
                    "evidence_kind": {"type": "string", "enum": ["numerator", "exclusion"]},
                    "evidence_type": {"type": "string"},
                    "service_date": {"type": "string"},
                    "value": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["document_id", "excerpt", "evidence_kind", "evidence_type",
                             "service_date", "value", "confidence", "reason"],
            },
        }
    },
    "required": ["findings"],
}


def build_prompt(gap: dict, doc_ids: list[str]) -> str:
    last = gap["last_event"]
    lines = [
        f"Gap ID: {gap['gap_id']}",
        f"Measure: {gap['measure']} ({gap['name']})",
        f"Measurement year: {MY}",
        f"Member: age {gap['age']}, {gap['sex']}",
        f"Missing: {gap['missing']}",
        f"Last known event: {last['value']} on {last['date']}" if last else "Last known event: none",
        "",
        "Rule:",
        rule_text(gap),
        "",
        "Documents:",
    ]
    for d in doc_ids:
        doc = DOCS[d]
        lines += [f"[DOC {d} | {doc['type']} | {doc['date']} | {doc['source']}]", doc["text"], ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- model call

def call_model(prompt: str) -> tuple[str, dict]:
    return call_ollama(prompt) if PROVIDER == "ollama" else call_hosted(prompt)


def call_hosted(prompt: str) -> tuple[str, dict]:
    """OpenAI-compatible chat completion (OpenRouter, Groq). Retries on rate limits."""
    url, key_var = HOSTED[PROVIDER]
    key = os.environ.get(key_var)
    if not key:
        raise SystemExit(f"Set {key_var} in this terminal first, e.g. $env:{key_var} = '<your key>'")
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "temperature": 0, "seed": 42, "max_tokens": 8192,  # reasoning models spend output tokens on thinking
        "response_format": {"type": "json_schema", "json_schema": {"name": "findings", "schema": SCHEMA}},
    }
    if PROVIDER == "groq" and "gpt-oss" in MODEL:
        body["reasoning_effort"] = "low"
    if PROVIDER == "groq" and "qwen" in MODEL:
        body["reasoning_format"] = "hidden"  # keep the model's thinking out of the JSON answer
    for attempt in range(1, 5):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {key}",
            # Cloudflare in front of Groq rejects the default Python-urllib agent with error 1010.
            "User-Agent": "hedis-evidence-demo/1.0 (+python)", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code == 429 and attempt < 4:
                wait = int(float(e.headers.get("retry-after") or 20)) + 1
                log("WAIT", f"rate limited (429), retrying in {wait}s (attempt {attempt})", YELLOW)
                time.sleep(wait)
                continue
            if e.code == 400 and body["response_format"]["type"] == "json_schema":
                log("WARN", "provider rejected the JSON schema option; retrying with plain JSON mode", YELLOW)
                body["response_format"] = {"type": "json_object"}
                continue
            raise SystemExit(f"{PROVIDER} returned HTTP {e.code}: {detail}")
    choice = data["choices"][0]
    text = (choice["message"].get("content") or "").strip()
    text = re.sub(r"(?s)<think>.*?</think>", "", text).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    print(f"{DIM}          {text}{RESET}", flush=True)  # phi-ok: prototype on made-up demo data
    if choice.get("finish_reason") == "length":
        log("WARN", f"answer was cut off at the {body['max_tokens']}-token output limit", YELLOW)
    usage = data.get("usage", {})
    return text, {"prompt_eval_count": usage.get("prompt_tokens"), "eval_count": usage.get("completion_tokens"),
                  "served_by": data.get("provider") or data.get("model"), "finish_reason": choice.get("finish_reason")}


def call_ollama(prompt: str) -> tuple[str, dict]:
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "format": SCHEMA, "stream": True, "think": False, "keep_alive": "30m",
        "options": {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 1024},
    }
    req = urllib.request.Request(OLLAMA_URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    text, stats = [], {}
    print(f"{DIM}          ", end="", flush=True)
    with urllib.request.urlopen(req, timeout=900) as resp:
        for raw in resp:
            chunk = json.loads(raw)
            piece = chunk.get("message", {}).get("content", "")
            text.append(piece)
            print(piece, end="", flush=True)
            if chunk.get("done"):
                stats = chunk
    print(RESET, flush=True)
    return "".join(text), stats


# ---------------------------------------------------------------- validation

def parse_date(s: str) -> tuple[date, date] | None:
    """Return the earliest and latest day a date string can mean."""
    s = s.strip()
    patterns = [
        (r"^(\d{4})-(\d{2})-(\d{2})$", lambda m: (int(m[1]), int(m[2]), int(m[3]))),
        (r"^(\d{1,2})/(\d{1,2})/(\d{4})$", lambda m: (int(m[3]), int(m[1]), int(m[2]))),
    ]
    for pat, f in patterns:
        m = re.match(pat, s)
        if m:
            y, mo, d = f(m)
            return date(y, mo, d), date(y, mo, d)
    m = re.match(r"^(\d{4})-(\d{2})$", s) or re.match(r"^(\d{1,2})/(\d{4})$", s)
    if m:
        y, mo = (int(m[1]), int(m[2])) if len(m[1]) == 4 else (int(m[2]), int(m[1]))
        end = date(y, 12, 31) if mo == 12 else date(y, mo + 1, 1).fromordinal(date(y, mo + 1, 1).toordinal() - 1)
        return date(y, mo, 1), end
    m = re.match(r"^(\d{4})$", s)
    if m:
        return date(int(m[1]), 1, 1), date(int(m[1]), 12, 31)
    return None


def locate_excerpt(doc_text: str, excerpt: str) -> tuple[int, int] | None:
    i = doc_text.find(excerpt)
    if i >= 0:
        return i, i + len(excerpt)
    def norm(t: str) -> str:
        return re.sub(r"\s+", " ", t).strip().lower()

    j = norm(doc_text).find(norm(excerpt))
    return (j, j + len(norm(excerpt))) if j >= 0 else None


ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "st", "vs", "approx", "no"}
MAX_EXCERPT_CHARS = 200
MIN_CONFIDENCE = 0.3


def split_sentences(text: str) -> list[str]:
    """Split into sentences, ignoring full stops after common abbreviations such as 'Dr.'."""
    parts, start = [], 0
    for m in re.finditer(r"([A-Za-z0-9)%]+)\.\s+(?=[A-Z])", text):
        if m[1].lower() not in ABBREVIATIONS:
            parts.append(text[start:m.end(1) + 1].strip())
            start = m.end()
    parts.append(text[start:].strip())
    return [p for p in parts if p]


def sentence_count(text: str) -> int:
    return len(split_sentences(text))


def narrow_excerpt(f: dict) -> str | None:
    """Pick the one sentence of a long excerpt that holds the finding's value or type; None if unclear."""
    sentences = split_sentences(f["excerpt"])
    keys = [k.lower() for k in re.findall(r"\d+(?:[./]\d+)?|[A-Za-z]{4,}", f"{f['value']} {f['evidence_type']}")]
    scored = sorted(((sum(k in s.lower() for k in keys), s) for s in sentences), reverse=True)
    if not scored or scored[0][0] == 0 or (len(scored) > 1 and scored[0][0] == scored[1][0]):
        return None
    return scored[0][1]


def in_years(start: date, end: date, lo: int, hi: int) -> bool:
    return start.year >= lo and end.year <= hi


def window_check(gap: dict, f: dict, when: tuple[date, date]) -> str | None:
    """Return a drop reason, or None if the finding passes the measure's date and value checks."""
    m, kind, etype = gap["measure"], f["evidence_kind"], f["evidence_type"].lower()
    if kind == "exclusion":
        return None
    start, end = when
    last = date.fromisoformat(gap["last_event"]["date"]) if gap["last_event"] else None
    if m == "SPD":
        return "SPD numerator comes from pharmacy fills, not notes"
    if m == "COL-E":
        text = (etype + " " + f["excerpt"]).lower()
        if "colonoscopy" in text and "colonograph" not in text:
            ok = in_years(start, end, MY - 9, MY)
        elif "dna" in text or "cologuard" in text:
            ok = in_years(start, end, MY - 2, MY)
        elif "sigmoid" in text or "colonograph" in text:
            ok = in_years(start, end, MY - 4, MY)
        elif "fit" in text or "fobt" in text or "immunochemical" in text:
            ok = in_years(start, end, MY, MY)
        else:
            return f"unknown COL-E test type '{f['evidence_type']}'"
        return None if ok else "date outside the test's look-back window"
    if m in ("CBP", "BPD"):
        if start != end:
            return "reading needs a full date to compare with the last known reading"
        if last and start <= last:
            return f"not newer than the last known reading ({last})"
        if end > YEAR_END:
            return "after the measurement year"
        bp = re.search(r"(\d{2,3})\s*/\s*(\d{2,3})", f["value"] + " " + f["excerpt"])
        if not bp:
            return "no systolic/diastolic pair"
        sys_, dia = int(bp[1]), int(bp[2])
        return None if sys_ < 140 and dia < 90 else f"{sys_}/{dia} is not under 140/90"
    if m == "GSD":
        if last and end <= last:
            return f"not newer than the last known result ({last})"
        val = re.search(r"(\d+(?:\.\d+)?)", f["value"])
        if not val:
            return "no numeric HbA1c or GMI value"
        return None if float(val[1]) < 8.0 else f"{val[1]}% is not under 8.0%"
    if m == "KED":
        if not in_years(start, end, MY, MY):
            return "test not in 2026"
        if ("uacr" in etype or "ratio" in etype) and not re.search(r"mg\s*/\s*g\b", f["value"] + " " + f["excerpt"], re.I):
            return "labelled uACR but the value is not a ratio in mg/g (a urine albumin alone needs a creatinine pair)"
        return None
    if m == "CCS-E":
        text = (etype + " " + f["excerpt"]).lower()
        if "hpv" in text:
            return None if in_years(start, end, MY - 4, MY) else "HPV test outside 2022 to 2026"
        if "pap" in text or "cytolog" in text:
            return None if in_years(start, end, MY - 2, MY) else "Pap outside 2024 to 2026"
        return f"unknown CCS-E test type '{f['evidence_type']}'"
    return None


def ked_pairing(kept: list[dict]) -> None:
    """Apply the 4-day albumin and creatinine pairing rule; mark unpaired parts as dropped."""
    alb = [k for k in kept if "albumin" in k["f"]["evidence_type"].lower() and "ratio" not in k["f"]["evidence_type"].lower()]
    cre = [k for k in kept if "creatinine" in k["f"]["evidence_type"].lower() and "ratio" not in k["f"]["evidence_type"].lower()]
    for a in alb:
        partner = next((c for c in cre if abs((c["when"][0] - a["when"][0]).days) <= 4), None)
        if partner is None:
            a["drop"] = "urine albumin has no urine creatinine within 4 days"
    for c in cre:
        partner = next((a for a in alb if abs((c["when"][0] - a["when"][0]).days) <= 4), None)
        if partner is None:
            c["drop"] = "urine creatinine has no urine albumin within 4 days"


def validate(gap: dict, findings: list[dict], sent: list[str]) -> list[dict]:
    results = []
    for f in findings:
        r = {"f": f, "drop": None, "offsets": None, "when": None, "narrowed_from": None, "long_quote": False}
        doc = DOCS.get(f["document_id"])
        conf = float(f.get("confidence") or 0)
        conf = conf / 100 if conf > 1 else conf
        if conf < MIN_CONFIDENCE:
            r["drop"] = f"model's own confidence is {conf:.2f}; it is saying this does not qualify"
        elif f["document_id"] not in sent or doc is None or doc["member"] != gap["member"]:
            r["drop"] = "document was not sent for this member"
        else:
            r["offsets"] = locate_excerpt(doc["text"], f["excerpt"])
            if r["offsets"] is None:
                r["drop"] = "excerpt not found word for word in the document"
            else:
                if len(f["excerpt"]) > MAX_EXCERPT_CHARS or sentence_count(f["excerpt"]) > 1:
                    short = narrow_excerpt(f)
                    if short is None:
                        r["long_quote"] = True
                    else:
                        r["narrowed_from"] = f["excerpt"]
                        f["excerpt"] = short
                        r["offsets"] = locate_excerpt(doc["text"], short)
                # Date and value checks run on every located quote, narrowed or not.
                r["when"] = parse_date(f["service_date"]) or parse_date(doc["date"])
                r["drop"] = window_check(gap, f, r["when"]) if r["when"] else "service date not readable"
        results.append(r)
    if gap["measure"] == "KED":
        ked_pairing([r for r in results if r["drop"] is None])
    return results


def gap_status(gap: dict, kept: list[dict]) -> str:
    if not kept:
        return "Open"
    if gap["measure"] == "KED" and not any(k["f"]["evidence_kind"] == "exclusion" for k in kept):
        types = " ".join(k["f"]["evidence_type"].lower() for k in kept)
        has_egfr = "egfr" in types
        has_uacr = "ratio" in types or "uacr" in types or ("albumin" in types and "creatinine" in types)
        if not (has_egfr and has_uacr):
            return "Pending review (1 of 2 KED tests found)"
    return "Pending review"


def score(gap: dict, results: list[dict]) -> tuple:
    """Compare validated findings with the gap's expected outcome."""
    kept = [r for r in results if not r["drop"]]
    status = gap_status(gap, kept)
    excerpts = [r["f"]["excerpt"].lower() for r in kept]
    wanted = [s.lower() for s in gap["expect"]["find"]]
    found = {s: any(s.lower() in e for e in excerpts) for s in gap["expect"]["find"]}
    # A trap leaks only when a finding is built on it, not when a correct finding's quote also mentions it.
    leaked = {s: any(s.lower() in e and not any(w in e for w in wanted) for e in excerpts)
              for s in gap["expect"]["avoid"]}
    status_ok = gap["expect"].get("status", status) == status
    passed = all(found.values()) and not any(leaked.values()) and status_ok
    return status, found, leaked, status_ok, passed


def recheck(gap_id: str, raw: str) -> dict:
    """Re-validate a saved model answer with the current rules (used by compare_runs.py)."""
    gap = next(g for g in GAPS if g["gap_id"] == gap_id)
    sent = [d for d, doc in DOCS.items() if doc["member"] == gap["member"]]
    try:
        findings = json.loads(raw).get("findings", []) if raw.strip() else []
    except ValueError:
        return {"status": "Failed", "passed": False, "results": [], "missed": ["unusable answer"], "leaked": []}
    results = validate(gap, findings, sent)
    status, found, leaked, status_ok, passed = score(gap, results)
    return {"status": status, "passed": passed, "results": results,
            "missed": [s for s, ok in found.items() if not ok], "leaked": [s for s, bad in leaked.items() if bad]}


# ---------------------------------------------------------------- run

def run_gap(gap: dict, logf) -> dict:
    print(f"\n{BOLD}{CYAN}=== {gap['gap_id']} · {gap['measure']} · {gap['name']} ==={RESET}", flush=True)
    last = gap["last_event"]
    log("INPUT", f"member {gap['member']} · age {gap['age']} · {gap['sex']} · "
                 f"last event: {last['value'] + ' on ' + last['date'] if last else 'none'}")
    log("MISSING", gap["missing"])
    sent = [d for d, doc in DOCS.items() if doc["member"] == gap["member"]]
    for d in sent:
        log("DOC", f"{d} · {DOCS[d]['type']} · {DOCS[d]['date']} · {DOCS[d]['source']}")
    prompt = build_prompt(gap, sent)
    log("PROMPT", f"{len(prompt):,} characters (about {len(prompt) // 4:,} tokens) plus system rules")
    where = "local Ollama, thinking off, streaming" if PROVIDER == "ollama" else PROVIDER
    log("MODEL", f"calling {MODEL} ({where}, temperature 0, seed 42), output:")
    t0 = time.time()
    raw, stats = call_model(prompt)
    took = time.time() - t0
    log("MODEL", f"done in {took:.1f}s · {stats.get('prompt_eval_count', '?')} prompt tokens · "
                 f"{stats.get('eval_count', '?')} output tokens", GREEN)

    try:
        if not raw.strip():
            raise ValueError("empty response")
        findings = json.loads(raw).get("findings", [])
    except ValueError as e:  # JSONDecodeError is a ValueError
        log("ERROR", f"unusable answer ({str(e)[:80]}): recorded as a failed attempt, not as 'no evidence'", RED)
        logf.write(json.dumps({
            "gap_id": gap["gap_id"], "model": MODEL, "seconds": round(took, 1),
            "prompt_tokens": stats.get("prompt_eval_count"), "output_tokens": stats.get("eval_count"),
            "system": SYSTEM, "prompt": prompt, "raw_response": raw, "findings": [],
            "status": "Failed", "passed": False, "error": str(e)[:200],
        }) + "\n")
        return {"gap": gap, "status": "Failed", "kept": [], "results": [], "seconds": took, "score": False}
    if not findings:
        log("FINDINGS", "model found no evidence", YELLOW)
    results = validate(gap, findings, sent)
    for i, r in enumerate(results, 1):
        f = r["f"]
        log("FINDING", f"#{i} {f['document_id']} \"{r['narrowed_from'] or f['excerpt']}\"")
        if r["narrowed_from"]:
            log("NARROW", f"quote was too long; narrowed to the sentence with the value: \"{f['excerpt']}\"", YELLOW)
        if r["long_quote"]:
            log("WARN", "long quote kept as is: no single sentence clearly holds the value; reviewer reads it all", YELLOW)
        log("", f"{f['evidence_kind']} · {f['evidence_type']} · date {f['service_date']} · value {f['value'] or '-'} · "
                f"confidence {f['confidence']}")
        log("", f"{DIM}reason: {f['reason']}{RESET}")
        if r["drop"]:
            log("CHECK", f"[x] dropped by validator: {r['drop']}", RED)
        else:
            s, e = r["offsets"]
            log("CHECK", f"[ok] excerpt at chars {s}-{e} · date and value pass", GREEN)
            log("SAVE", "PROPOSED -> abstractor review queue", GREEN)

    kept = [r for r in results if not r["drop"]]
    status, found, leaked, status_ok, passed = score(gap, results)
    log("STATUS", status, BOLD)
    for s, ok in found.items():
        log("EXPECT", f"{'[ok]' if ok else '[x] missed'} should find \"{s}\"", GREEN if ok else RED)
    for s, bad in leaked.items():
        log("EXPECT", f"{'[x] reached the queue' if bad else '[ok] kept out'}: trap \"{s}\"", RED if bad else GREEN)
    if "status" in gap["expect"]:
        log("EXPECT", f"{'[ok]' if status_ok else '[x]'} status should be \"{gap['expect']['status']}\"",
            GREEN if status_ok else RED)

    logf.write(json.dumps({
        "gap_id": gap["gap_id"], "model": MODEL, "seconds": round(took, 1),
        "prompt_tokens": stats.get("prompt_eval_count"), "output_tokens": stats.get("eval_count"),
        "system": SYSTEM, "prompt": prompt, "raw_response": raw,
        "findings": [{**r["f"], "dropped": r["drop"], "offsets": r["offsets"], "narrowed_from": r["narrowed_from"]}
                     for r in results],
        "status": status, "passed": passed,
    }) + "\n")
    return {"gap": gap, "status": status, "kept": kept, "results": results, "seconds": took, "score": passed}


def main() -> None:
    print(f"{BOLD}HEDIS evidence extraction demo{RESET} · {PROVIDER} · model {MODEL} · measurement year {MY} · {len(GAPS)} gaps")
    print(f"{DIM}Full prompts and raw responses are written to {LOG_PATH}{RESET}")
    out = []
    with LOG_PATH.open("w", encoding="utf-8") as logf:
        for gap in GAPS:
            out.append(run_gap(gap, logf))

    print(f"\n{BOLD}=== Summary ==={RESET}")
    print(f"{'Gap':<8}{'Measure':<8}{'Status':<42}{'Proposed':>9}{'Dropped':>9}{'Seconds':>9}  Result")
    for r in out:
        dropped = len([x for x in r["results"] if x["drop"]])
        verdict = f"{GREEN}pass{RESET}" if r["score"] else f"{RED}check{RESET}"
        print(f"{r['gap']['gap_id']:<8}{r['gap']['measure']:<8}{r['status']:<42}{len(r['kept']):>9}{dropped:>9}"
              f"{r['seconds']:>9.1f}  {verdict}")
    total = sum(r["seconds"] for r in out)
    passed = sum(1 for r in out if r["score"])
    print(f"\n{passed} of {len(out)} gaps matched the expected outcome · total {total:.0f}s · "
          f"average {total / len(out):.0f}s per gap")


if __name__ == "__main__":
    main()
