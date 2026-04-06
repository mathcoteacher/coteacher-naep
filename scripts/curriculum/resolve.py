"""Resolver: choose final K-5 and 6-8 curriculum values from all evidence.

For each district + grade band, examines all extraction_candidates and picks
the best value based on source trust + recency + confidence.

Source trust hierarchy:
  state_dashboard > district_website > cemd > board_minutes > procurement > web_search > inference

Usage:
    python scripts/curriculum/resolve.py
"""

import json
import os
import sqlite3
import sys
from datetime import date

DB_PATH = os.path.join(os.path.dirname(__file__), "curriculum.db")
TODAY = date.today().isoformat()

# Source trust scores (higher = more trusted)
SOURCE_TRUST = {
    "state_dashboard": 1.0,
    "district_website": 0.9,
    "cemd": 0.8,
    "board_minutes": 0.85,
    "procurement": 0.85,
    "state_doe": 0.75,
    "nces_directory": 0.7,
    "web_scrape": 0.5,
    "web_search": 0.5,
    "pattern_match": 0.5,
    "inference": 0.3,
    "unknown": 0.2,
}


def compute_evidence_score(source_type, confidence):
    """Compute a combined score for evidence ranking.

    score = trust_weight * confidence
    """
    trust = SOURCE_TRUST.get(source_type, 0.2)
    return trust * confidence


def resolve_all(conn):
    """Resolve curriculum for all districts with extraction candidates.

    For each (leaid, grade_band) combination:
    1. Gather all candidates
    2. Score each
    3. Check for consensus (multiple sources agree)
    4. Pick the best
    """
    cur = conn.cursor()

    # Rebuild resolved table from current evidence to avoid stale/orphaned rows.
    cur.execute("DELETE FROM resolved_curriculum")
    conn.commit()

    # Get all unique (leaid, grade_band) combinations with candidates
    cur.execute("""
        SELECT DISTINCT leaid, grade_band FROM extraction_candidates
        ORDER BY leaid, grade_band
    """)
    combos = cur.fetchall()
    print(f"  Resolving {len(combos)} (leaid, grade_band) combinations...")

    resolved_count = 0
    consensus_count = 0
    single_source_count = 0

    for leaid, grade_band in combos:
        cur.execute("""
            SELECT id, curriculum_normalized, curriculum_raw, source_type,
                   confidence, date_collected
            FROM extraction_candidates
            WHERE leaid = ? AND grade_band = ?
            ORDER BY confidence DESC
        """, (leaid, grade_band))
        candidates = cur.fetchall()

        if not candidates:
            continue

        # Score each candidate
        scored = []
        for cand in candidates:
            cid, norm, raw, stype, conf, collected = cand
            score = compute_evidence_score(stype, conf)
            scored.append({
                "id": cid,
                "curriculum": norm or raw,
                "source_type": stype,
                "confidence": conf,
                "score": score,
            })

        # Sort by score descending
        scored.sort(key=lambda x: -x["score"])

        # Check for consensus: do multiple sources agree on the same curriculum?
        curriculum_votes = {}
        for s in scored:
            name = s["curriculum"]
            if name:
                if name not in curriculum_votes:
                    curriculum_votes[name] = []
                curriculum_votes[name].append(s)

        best = scored[0]
        method = "single_source"
        final_confidence = best["confidence"]
        candidate_ids = [best["id"]]

        # Check consensus
        if best["curriculum"] in curriculum_votes:
            agreeing = curriculum_votes[best["curriculum"]]
            if len(agreeing) > 1:
                method = "consensus"
                # Boost confidence for consensus
                final_confidence = min(1.0, best["confidence"] * 1.1)
                candidate_ids = [s["id"] for s in agreeing]
                consensus_count += 1
            else:
                single_source_count += 1
        else:
            single_source_count += 1

        # Determine status
        if best["source_type"] in ("state_dashboard",) and best["confidence"] >= 0.9:
            status = "verified"
        elif best["source_type"] in (
            "cemd",
            "district_website",
            "board_minutes",
            "procurement",
            "state_doe",
            "nces_directory",
        ):
            status = "verified" if final_confidence >= 0.7 else "inferred"
        elif best["source_type"] in ("web_scrape", "web_search", "pattern_match"):
            # Never auto-verify weak web matches unless confidence is extremely high.
            status = "verified" if final_confidence >= 0.9 else "inferred"
        else:
            status = "inferred" if final_confidence < 0.8 else "verified"

        # Insert/update resolved
        cur.execute("""
            INSERT OR REPLACE INTO resolved_curriculum
            (leaid, grade_band, curriculum_normalized, status, confidence,
             source_candidate_ids, resolution_method, resolved_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (leaid, grade_band, best["curriculum"], status,
              round(final_confidence, 3),
              json.dumps(candidate_ids), method, TODAY))
        resolved_count += 1

    conn.commit()
    return resolved_count, consensus_count, single_source_count


def print_resolution_stats(conn):
    """Print resolved curriculum statistics."""
    cur = conn.cursor()

    print("\n=== Resolution Stats ===")

    cur.execute("SELECT COUNT(*) FROM resolved_curriculum")
    total = cur.fetchone()[0]
    print(f"Total resolved entries: {total}")

    cur.execute("SELECT grade_band, COUNT(*) FROM resolved_curriculum GROUP BY grade_band")
    for band, cnt in cur.fetchall():
        print(f"  {band}: {cnt}")

    cur.execute("SELECT status, COUNT(*) FROM resolved_curriculum GROUP BY status")
    for status, cnt in cur.fetchall():
        print(f"  {status}: {cnt}")

    cur.execute("SELECT resolution_method, COUNT(*) FROM resolved_curriculum GROUP BY resolution_method")
    for method, cnt in cur.fetchall():
        print(f"  method={method}: {cnt}")

    cur.execute("SELECT COUNT(DISTINCT leaid) FROM resolved_curriculum")
    unique = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM districts")
    total_districts = cur.fetchone()[0]
    print(f"\nDistricts with any resolved data: {unique}/{total_districts} ({unique/total_districts*100:.1f}%)")

    # Both grade bands
    cur.execute("""
        SELECT COUNT(*) FROM (
            SELECT leaid FROM resolved_curriculum WHERE grade_band = 'k5'
            INTERSECT
            SELECT leaid FROM resolved_curriculum WHERE grade_band = '68'
        )
    """)
    both = cur.fetchone()[0]
    print(f"Districts with both K-5 and 6-8: {both}")

    # Top curricula
    cur.execute("""
        SELECT curriculum_normalized, COUNT(*) FROM resolved_curriculum
        WHERE grade_band = 'k5'
        GROUP BY curriculum_normalized ORDER BY COUNT(*) DESC LIMIT 10
    """)
    print(f"\nTop K-5 curricula:")
    for name, cnt in cur.fetchall():
        print(f"  {name}: {cnt}")

    cur.execute("""
        SELECT curriculum_normalized, COUNT(*) FROM resolved_curriculum
        WHERE grade_band = '68'
        GROUP BY curriculum_normalized ORDER BY COUNT(*) DESC LIMIT 10
    """)
    print(f"\nTop 6-8 curricula:")
    for name, cnt in cur.fetchall():
        print(f"  {name}: {cnt}")


def main():
    print(f"Database: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)

    print("\nResolving curriculum from all evidence...")
    resolved, consensus, single = resolve_all(conn)
    print(f"  Resolved: {resolved}")
    print(f"  Consensus: {consensus}")
    print(f"  Single source: {single}")

    # Log run
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO run_logs (run_date, phase, status, districts_found, notes)
        VALUES (?, 'resolution', 'success', ?, ?)
    """, (TODAY, resolved, f"consensus={consensus}, single={single}"))
    conn.commit()

    print_resolution_stats(conn)
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
