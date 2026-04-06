"""Export curriculum data to public/data/curriculum.json for the app.

Reads from the resolved_curriculum and extraction_candidates tables
to generate both:
- Backward-compatible output at public/data/curriculum.json
- Richer debug artifact with provenance at public/data/curriculum_debug.json

Usage:
    python scripts/curriculum/export.py
"""

import json
import os
import sqlite3
from datetime import date

DB_PATH = os.path.join(os.path.dirname(__file__), "curriculum.db")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "public", "data", "curriculum.json")


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Get total district count
    cur.execute("SELECT COUNT(*) FROM districts")
    total_districts = cur.fetchone()[0]

    # Get resolved curriculum data with district info
    cur.execute("""
        SELECT d.leaid, d.district_name, d.state, d.school_count,
               k5.curriculum_normalized as k5, k5.status as k5_status,
               k5.confidence as k5_conf, k5.resolution_method as k5_method,
               k5.source_candidate_ids as k5_sources,
               g68.curriculum_normalized as g68, g68.status as g68_status,
               g68.confidence as g68_conf, g68.resolution_method as g68_method,
               g68.source_candidate_ids as g68_sources
        FROM districts d
        LEFT JOIN resolved_curriculum k5
            ON d.leaid = k5.leaid AND k5.grade_band = 'k5'
        LEFT JOIN resolved_curriculum g68
            ON d.leaid = g68.leaid AND g68.grade_band = '68'
        ORDER BY d.state, d.district_name
    """)

    districts = {}
    districts_debug = {}

    for row in cur.fetchall():
        leaid = row[0]

        # Backward-compatible format (minimal)
        entry = {
            "name": row[1],
            "state": row[2],
        }
        if row[3]:
            entry["schools"] = row[3]
        if row[4]:  # k5
            entry["k5"] = row[4]
        if row[9]:  # g68
            entry["g68"] = row[9]
        if row[5]:
            entry["k5_status"] = row[5]
        if row[10]:
            entry["g68_status"] = row[10]
        if row[6] is not None:
            entry["k5_confidence"] = round(row[6], 3)
        if row[11] is not None:
            entry["g68_confidence"] = round(row[11], 3)

        # Minimum confidence for the entry
        confs = [c for c in [row[6], row[11]] if c is not None]
        if confs:
            min_conf = min(confs)
            if min_conf < 1.0:
                entry["confidence"] = round(min_conf, 3)

        # Source: use the most specific source type from candidates
        k5_status = row[5]
        g68_status = row[10]
        if k5_status == "verified" or g68_status == "verified":
            # Look up actual source type from candidates
            source_ids = []
            for src_json in [row[8], row[13]]:
                if src_json:
                    try:
                        source_ids.extend(json.loads(src_json))
                    except (json.JSONDecodeError, TypeError):
                        pass

            if source_ids:
                cur2 = conn.cursor()
                placeholders = ",".join("?" * len(source_ids))
                cur2.execute(f"""
                    SELECT DISTINCT source_type FROM extraction_candidates
                    WHERE id IN ({placeholders})
                """, source_ids)
                source_types = [r[0] for r in cur2.fetchall()]
                if "state_dashboard" in source_types:
                    entry["source"] = "state_dashboard"
                elif "cemd" in source_types:
                    entry["source"] = "cemd"
                elif "web_search" in source_types:
                    entry["source"] = "web_scrape"
                elif "inference" in source_types:
                    entry["source"] = "inferred"
                else:
                    entry["source"] = source_types[0] if source_types else "inferred"
            else:
                entry["source"] = "inferred"
        else:
            entry["source"] = "inferred"

        districts[leaid] = entry

        # Rich debug format
        debug_entry = dict(entry)
        debug_entry["k5_status"] = k5_status or "missing"
        debug_entry["g68_status"] = g68_status or "missing"
        debug_entry["k5_confidence"] = round(row[6], 3) if row[6] else None
        debug_entry["g68_confidence"] = round(row[11], 3) if row[11] else None
        debug_entry["k5_method"] = row[7]
        debug_entry["g68_method"] = row[12]
        districts_debug[leaid] = debug_entry

    # Coverage stats
    k5_count = sum(1 for d in districts.values() if "k5" in d)
    g68_count = sum(1 for d in districts.values() if "g68" in d)
    verified_count = sum(
        1 for d in districts_debug.values()
        if d.get("k5_status") == "verified" or d.get("g68_status") == "verified"
    )
    inferred_count = sum(
        1 for d in districts_debug.values()
        if d.get("k5_status") == "inferred" and d.get("g68_status") == "inferred"
    )

    # Curriculum frequency from resolved data
    cur.execute("""
        SELECT curriculum_normalized, COUNT(*) FROM resolved_curriculum
        WHERE grade_band = 'k5'
        GROUP BY curriculum_normalized ORDER BY COUNT(*) DESC LIMIT 15
    """)
    top_k5 = {row[0]: row[1] for row in cur.fetchall()}

    cur.execute("""
        SELECT curriculum_normalized, COUNT(*) FROM resolved_curriculum
        WHERE grade_band = '68'
        GROUP BY curriculum_normalized ORDER BY COUNT(*) DESC LIMIT 15
    """)
    top_g68 = {row[0]: row[1] for row in cur.fetchall()}

    # Build backward-compatible output
    output = {
        "meta": {
            "generated": date.today().isoformat(),
            "total_districts": total_districts,
            "districts_with_data": len(districts),
            "k5_coverage": k5_count,
            "g68_coverage": g68_count,
            "coverage_pct": round(len(districts) / total_districts * 100, 1),
            "verified_districts": verified_count,
            "inferred_districts": inferred_count,
            "verified_entries": sum(
                1 for d in districts_debug.values()
                if d.get("k5_status") == "verified"
            ) + sum(
                1 for d in districts_debug.values()
                if d.get("g68_status") == "verified"
            ),
            "inferred_entries": sum(
                1 for d in districts_debug.values()
                if d.get("k5_status") == "inferred"
            ) + sum(
                1 for d in districts_debug.values()
                if d.get("g68_status") == "inferred"
            ),
            "states_covered": sorted(set(d["state"] for d in districts.values())),
            "top_k5_curricula": top_k5,
            "top_g68_curricula": top_g68,
        },
        "districts": districts,
    }

    # Build rich debug output
    output_debug = {
        "meta": dict(output["meta"]),
        "districts": districts_debug,
    }

    # Add provenance summary to debug meta
    cur.execute("SELECT source_type, COUNT(*) FROM extraction_candidates GROUP BY source_type")
    output_debug["meta"]["evidence_by_source"] = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute("SELECT COUNT(*) FROM extraction_candidates")
    output_debug["meta"]["total_evidence_items"] = cur.fetchone()[0]

    # Write backward-compatible output
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, separators=(",", ":"))

    # Write rich debug version
    debug_path = OUTPUT_PATH.replace(".json", "_debug.json")
    with open(debug_path, "w", encoding="utf-8") as f:
        json.dump(output_debug, f, indent=2)

    file_size = os.path.getsize(OUTPUT_PATH)
    debug_size = os.path.getsize(debug_path)
    print(f"Exported to {OUTPUT_PATH}")
    print(f"  File size: {file_size:,} bytes ({file_size/1024:.1f} KB)")
    print(f"  Districts: {len(districts)}")
    print(f"  K-5 coverage: {k5_count}")
    print(f"  6-8 coverage: {g68_count}")
    print(f"  Verified: {verified_count}")
    print(f"  Inferred: {inferred_count}")
    print(f"  States: {', '.join(output['meta']['states_covered'])}")
    print(f"\nDebug version: {debug_path} ({debug_size:,} bytes)")

    conn.close()


if __name__ == "__main__":
    main()
