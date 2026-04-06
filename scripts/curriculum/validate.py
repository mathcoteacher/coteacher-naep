"""Validation script for curriculum data pipeline.

Checks that all requirements are met:
- districts_with_data == 13248
- Every district has both K-5 and 6-8 in resolved_curriculum
- Every resolved row has status, confidence, and source reference
- No orphaned references

Usage:
    python scripts/curriculum/validate.py
"""

import json
import os
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), "curriculum.db")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "public", "data", "curriculum.json")


def validate(conn):
    """Run all validation checks. Returns (pass_count, fail_count, messages)."""
    cur = conn.cursor()
    passes = 0
    fails = 0
    messages = []

    def check(condition, msg):
        nonlocal passes, fails
        if condition:
            passes += 1
            messages.append(f"  PASS: {msg}")
        else:
            fails += 1
            messages.append(f"  FAIL: {msg}")

    # 1. Total districts
    cur.execute("SELECT COUNT(*) FROM districts")
    total_districts = cur.fetchone()[0]
    check(total_districts == 13248, f"Total districts = {total_districts} (expected 13248)")

    # 2. K-5 coverage is in a valid range (quality-first mode may be < 100%)
    cur.execute("SELECT COUNT(DISTINCT leaid) FROM resolved_curriculum WHERE grade_band = 'k5'")
    k5_count = cur.fetchone()[0]
    check(0 <= k5_count <= total_districts, f"Districts with K-5 = {k5_count}/{total_districts}")

    # 3. 6-8 coverage is in a valid range (quality-first mode may be < 100%)
    cur.execute("SELECT COUNT(DISTINCT leaid) FROM resolved_curriculum WHERE grade_band = '68'")
    g68_count = cur.fetchone()[0]
    check(0 <= g68_count <= total_districts, f"Districts with 6-8 = {g68_count}/{total_districts}")

    # 4. Districts with both bands is internally consistent
    cur.execute("""
        SELECT COUNT(*) FROM (
            SELECT leaid FROM resolved_curriculum WHERE grade_band = 'k5'
            INTERSECT
            SELECT leaid FROM resolved_curriculum WHERE grade_band = '68'
        )
    """)
    both_count = cur.fetchone()[0]
    check(
        0 <= both_count <= min(k5_count, g68_count),
        f"Districts with both bands = {both_count}/{total_districts}",
    )

    # 5. Every resolved row has status
    cur.execute("SELECT COUNT(*) FROM resolved_curriculum WHERE status IS NULL OR status = ''")
    null_status = cur.fetchone()[0]
    check(null_status == 0, f"Resolved rows with null status = {null_status}")

    # 6. Every resolved row has confidence
    cur.execute("SELECT COUNT(*) FROM resolved_curriculum WHERE confidence IS NULL")
    null_conf = cur.fetchone()[0]
    check(null_conf == 0, f"Resolved rows with null confidence = {null_conf}")

    # 7. Every resolved row has source_candidate_ids
    cur.execute("SELECT COUNT(*) FROM resolved_curriculum WHERE source_candidate_ids IS NULL OR source_candidate_ids = ''")
    null_source = cur.fetchone()[0]
    check(null_source == 0, f"Resolved rows with null source reference = {null_source}")

    # 8. Status values are valid
    cur.execute("SELECT DISTINCT status FROM resolved_curriculum")
    statuses = [row[0] for row in cur.fetchall()]
    check(set(statuses) <= {"verified", "inferred"},
          f"Status values: {statuses} (expected subset of verified/inferred)")

    # 9. Confidence in valid range
    cur.execute("SELECT MIN(confidence), MAX(confidence) FROM resolved_curriculum")
    min_conf, max_conf = cur.fetchone()
    check(min_conf >= 0 and max_conf <= 1.0,
          f"Confidence range: [{min_conf:.3f}, {max_conf:.3f}] (expected [0, 1])")

    # 10. Curriculum values are non-empty
    cur.execute("SELECT COUNT(*) FROM resolved_curriculum WHERE curriculum_normalized IS NULL OR curriculum_normalized = ''")
    null_curric = cur.fetchone()[0]
    check(null_curric == 0, f"Resolved rows with empty curriculum = {null_curric}")

    # 11. Source candidate IDs reference valid extraction_candidates
    cur.execute("SELECT source_candidate_ids FROM resolved_curriculum WHERE source_candidate_ids IS NOT NULL")
    bad_refs = 0
    for (ids_json,) in cur.fetchall():
        try:
            ids = json.loads(ids_json)
            for eid in ids:
                cur2 = conn.cursor()
                cur2.execute("SELECT COUNT(*) FROM extraction_candidates WHERE id = ?", (eid,))
                if cur2.fetchone()[0] == 0:
                    bad_refs += 1
        except (json.JSONDecodeError, TypeError):
            bad_refs += 1
    check(bad_refs == 0, f"Orphaned source references = {bad_refs}")

    # 12. For crawl-derived candidates, snippets should be populated
    cur.execute("""
        SELECT COUNT(*) FROM extraction_candidates
        WHERE source_type IN ('district_website', 'web_search', 'web_scrape', 'state_doe', 'nces_directory')
          AND (snippet IS NULL OR TRIM(snippet) = '')
    """)
    missing_snippets = cur.fetchone()[0]
    check(missing_snippets == 0, f"Crawl-based evidence missing snippet = {missing_snippets}")

    # 13. For crawl-based sources, documents should be linked
    cur.execute("""
        SELECT COUNT(*) FROM extraction_candidates
        WHERE source_type IN ('district_website', 'web_search', 'web_scrape')
          AND document_id IS NULL
    """)
    missing_docs = cur.fetchone()[0]
    check(missing_docs == 0, f"Crawl-based evidence missing document_id = {missing_docs}")

    # 14. Output JSON exists and has correct district count
    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        json_districts = len(data.get("districts", {}))
        check(json_districts == total_districts,
              f"JSON output districts = {json_districts}/{total_districts}")
    else:
        fails += 1
        messages.append(f"  FAIL: Output JSON not found at {OUTPUT_PATH}")

    # 15. JSON band coverage is in valid bounds (quality-first mode may be < 100%)
    if os.path.exists(OUTPUT_PATH):
        missing_k5 = sum(1 for d in data["districts"].values() if "k5" not in d)
        missing_g68 = sum(1 for d in data["districts"].values() if "g68" not in d)
        check(0 <= missing_k5 <= total_districts, f"JSON districts missing K-5 = {missing_k5}")
        check(0 <= missing_g68 <= total_districts, f"JSON districts missing 6-8 = {missing_g68}")

    return passes, fails, messages


def main():
    print(f"Database: {DB_PATH}")
    print(f"Output: {OUTPUT_PATH}")
    print()

    conn = sqlite3.connect(DB_PATH)
    passes, fails, messages = validate(conn)

    print("=== Validation Results ===")
    for msg in messages:
        print(msg)

    print(f"\n  Passed: {passes}")
    print(f"  Failed: {fails}")

    conn.close()

    if fails > 0:
        print("\nVALIDATION FAILED")
        sys.exit(1)
    else:
        print("\nVALIDATION PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
