"""Check the US-02 conversion results in the database against the seed data's expected outcomes.

Usage (after `python -m services.document_intake.cli process-pending`):
    python scripts/verify_intake.py

Reports file and document statuses, the member-match queue, structured rows, and most importantly whether
every evidence quote the AI step must find (data/seed/expected) is present in the converted page text.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.document_intake.db import connect  # noqa: E402

SEED = ROOT / "data" / "seed"


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def main() -> int:
    expected = json.loads((SEED / "expected" / "expected_findings.json").read_text(encoding="utf-8"))
    structured_expected = json.loads((SEED / "structured" / "structured_expected.json").read_text(encoding="utf-8"))
    problems = []
    with connect() as conn:
        q = lambda sql, *a: conn.execute(sql, a).fetchall()  # noqa: E731

        print("Files by processing status:", dict(q("SELECT processing_status, count(*) FROM source_files GROUP BY 1")))
        print("Documents by conversion status:", dict(q("SELECT conversion_status, count(*) FROM document_conversion GROUP BY 1")))
        print("Pages by method:", dict(q("SELECT method, count(*) FROM document_pages GROUP BY 1")))
        flagged = q("""SELECT p.document_reference_id, p.page_no, p.handwritten, p.low_confidence, round(p.confidence, 2),
                              p.ocr_engine
                       FROM document_pages p
                       WHERE p.handwritten OR p.low_confidence ORDER BY 1, 2""")
        print("\nHandwritten or low-confidence pages:")
        for row in flagged:
            print("  ", row)
        print("\nFailed or manual-review items:")
        for row in q("""SELECT 'file', original_name, processing_status, processing_note FROM source_files
                         WHERE processing_status IN ('failed','rejected')
                        UNION ALL SELECT 'document', document_reference_id, conversion_status, conversion_note
                          FROM document_conversion
                         WHERE conversion_status IN ('failed','manual_review')"""):
            print("  ", row)
        print("\nMember-match queue:")
        for row in q("""SELECT f.original_name, f.match_status, p.mrn FROM source_files f
                        LEFT JOIN patient p ON p.patient_id = f.patient_id WHERE f.match_status <> 'matched'
                        OR f.original_name IN ('D-502.jpg', 'D-9001.jpg') ORDER BY 1"""):
            print("  ", row)
        print("\nNear-duplicate links:", q("SELECT document_reference_id, near_duplicate_of FROM document_conversion "
                                           "WHERE near_duplicate_of IS NOT NULL"))

        print("\nStructured rows:")
        rows = {(r[0], r[1]): (r[2], r[3]) for r in q(
            """SELECT f.original_name, s.row_number, s.status, s.reject_reason FROM structured_records s
               JOIN source_files f ON f.id = s.source_file_id""")}
        for exp in structured_expected:
            got = rows.get((exp["file"], exp["row"]))
            want = {"load": "loaded", "reject": "rejected", "free_text_to_ai": "free_text_to_ai"}[exp["expect"]]
            ok = got is not None and got[0] == want
            print(f"   {'ok  ' if ok else 'FAIL'} {exp['file']} row {exp['row']}: expected {want}, got {got}")
            if not ok:
                problems.append(f"structured {exp['file']} row {exp['row']}")

        print("\nEvidence quotes present in converted page text:")
        page_text = {}
        for doc_id, text in q("SELECT document_reference_id, string_agg(text, ' ' ORDER BY page_no) FROM document_pages "
                           "GROUP BY 1"):
            page_text[doc_id] = norm(text)
        found = total = 0
        for gap in expected:
            for item in gap.get("find", []) + gap.get("optional", []):
                total += 1
                hit = norm(item["quote"]) in page_text.get(item["doc"], "")
                found += hit
                if not hit:
                    problems.append(f"{gap['gap_id']} quote missing in {item['doc']}: {item['quote']}")
                print(f"   {'ok  ' if hit else 'MISS'} {gap['gap_id']} {item['doc']}: {item['quote']}",
                      # phi-ok: developer CLI on synthetic test documents
                      )
        print(f"\n{found} of {total} evidence quotes are in the converted text.")

    if problems:
        print("\nProblems:")
        for p in problems:
            print("  -", p)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
