"""Migrate curriculum.db from flat single-row schema to evidence-first schema.

Adds tables for source registry, documents, extraction candidates,
resolved curriculum, and run logs. Migrates existing data from the
legacy `curriculum` table into the new evidence tables.

The legacy `curriculum` table is preserved for backward compat.

Usage:
    python scripts/curriculum/migrate.py
"""

import json
import os
import sqlite3
import sys
from datetime import date

DB_PATH = os.path.join(os.path.dirname(__file__), "curriculum.db")
TODAY = date.today().isoformat()


def create_new_tables(conn):
    """Create the evidence-first schema tables."""
    cur = conn.cursor()
    cur.executescript("""
        -- Source registry: known data sources per LEA
        CREATE TABLE IF NOT EXISTS source_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            leaid TEXT NOT NULL,
            source_type TEXT NOT NULL,
            url TEXT NOT NULL,
            domain TEXT,
            platform_hint TEXT,
            last_crawled TEXT,
            crawl_status TEXT DEFAULT 'pending',
            FOREIGN KEY (leaid) REFERENCES districts(leaid)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_source_registry_leaid_url
            ON source_registry(leaid, url);
        CREATE INDEX IF NOT EXISTS idx_source_registry_domain
            ON source_registry(domain);

        -- Documents: raw pages/PDFs fetched
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER REFERENCES source_registry(id),
            url TEXT NOT NULL,
            fetch_date TEXT NOT NULL,
            content_type TEXT,
            content_hash TEXT,
            snippet TEXT,
            status TEXT DEFAULT 'fetched'
        );
        CREATE INDEX IF NOT EXISTS idx_documents_url ON documents(url);
        CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_id);

        -- Extraction candidates: individual evidence items per LEA + grade band
        CREATE TABLE IF NOT EXISTS extraction_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            leaid TEXT NOT NULL,
            grade_band TEXT NOT NULL CHECK(grade_band IN ('k5', '68')),
            curriculum_raw TEXT NOT NULL,
            curriculum_normalized TEXT,
            source_type TEXT NOT NULL,
            source_url TEXT,
            document_id INTEGER REFERENCES documents(id),
            snippet TEXT,
            confidence REAL NOT NULL,
            extraction_method TEXT,
            date_collected TEXT NOT NULL,
            FOREIGN KEY (leaid) REFERENCES districts(leaid)
        );
        CREATE INDEX IF NOT EXISTS idx_ec_leaid ON extraction_candidates(leaid);
        CREATE INDEX IF NOT EXISTS idx_ec_grade_band ON extraction_candidates(grade_band);
        CREATE INDEX IF NOT EXISTS idx_ec_leaid_grade
            ON extraction_candidates(leaid, grade_band);

        -- Resolved curriculum: final chosen value per LEA + grade band
        CREATE TABLE IF NOT EXISTS resolved_curriculum (
            leaid TEXT NOT NULL,
            grade_band TEXT NOT NULL CHECK(grade_band IN ('k5', '68')),
            curriculum_normalized TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('verified', 'inferred')),
            confidence REAL NOT NULL,
            source_candidate_ids TEXT,
            resolution_method TEXT,
            resolved_date TEXT NOT NULL,
            PRIMARY KEY (leaid, grade_band),
            FOREIGN KEY (leaid) REFERENCES districts(leaid)
        );
        CREATE INDEX IF NOT EXISTS idx_rc_status ON resolved_curriculum(status);

        -- Run logs
        CREATE TABLE IF NOT EXISTS run_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            phase TEXT NOT NULL,
            status TEXT NOT NULL,
            districts_processed INTEGER DEFAULT 0,
            districts_found INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            duration_seconds REAL,
            notes TEXT
        );
    """)
    conn.commit()


def migrate_legacy_data(conn):
    """Migrate existing curriculum table rows into the new evidence tables.

    Each legacy row becomes:
    - 1 or 2 extraction_candidates (one per grade band with data)
    - 1 or 2 resolved_curriculum entries
    """
    cur = conn.cursor()

    # Check if we already migrated
    cur.execute("SELECT COUNT(*) FROM extraction_candidates")
    if cur.fetchone()[0] > 0:
        print("  Extraction candidates already populated — skipping migration")
        return

    cur.execute("""
        SELECT leaid, k5_curriculum, k5_normalized,
               grade68_curriculum, grade68_normalized,
               source_tier, source_url, confidence, date_collected
        FROM curriculum
    """)
    rows = cur.fetchall()

    tier_to_source_type = {
        1: "state_dashboard",
        2: "cemd",
        3: "web_scrape",
    }
    tier_to_method = {
        1: "api",
        2: "geospatial",
        3: "pattern_match",
    }

    ec_count = 0
    rc_count = 0

    for row in rows:
        leaid = row[0]
        k5_raw, k5_norm = row[1], row[2]
        g68_raw, g68_norm = row[3], row[4]
        tier = row[5]
        source_url = row[6]
        confidence = row[7] or 1.0
        date_collected = row[8]

        source_type = tier_to_source_type.get(tier, "unknown")
        method = tier_to_method.get(tier, "unknown")

        # For tier 1, status is verified; tier 2/3 depends on confidence
        status = "verified" if tier == 1 else ("verified" if confidence >= 0.8 else "inferred")

        # K-5 extraction candidate + resolved
        if k5_raw:
            cur.execute("""
                INSERT INTO extraction_candidates
                (leaid, grade_band, curriculum_raw, curriculum_normalized,
                 source_type, source_url, confidence, extraction_method, date_collected)
                VALUES (?, 'k5', ?, ?, ?, ?, ?, ?, ?)
            """, (leaid, k5_raw, k5_norm, source_type, source_url, confidence, method, date_collected))
            ec_id = cur.lastrowid
            ec_count += 1

            cur.execute("""
                INSERT OR REPLACE INTO resolved_curriculum
                (leaid, grade_band, curriculum_normalized, status, confidence,
                 source_candidate_ids, resolution_method, resolved_date)
                VALUES (?, 'k5', ?, ?, ?, ?, 'single_source', ?)
            """, (leaid, k5_norm or k5_raw, status, confidence,
                  json.dumps([ec_id]), TODAY))
            rc_count += 1

        # 6-8 extraction candidate + resolved
        if g68_raw:
            cur.execute("""
                INSERT INTO extraction_candidates
                (leaid, grade_band, curriculum_raw, curriculum_normalized,
                 source_type, source_url, confidence, extraction_method, date_collected)
                VALUES (?, '68', ?, ?, ?, ?, ?, ?, ?)
            """, (leaid, g68_raw, g68_norm, source_type, source_url, confidence, method, date_collected))
            ec_id = cur.lastrowid
            ec_count += 1

            cur.execute("""
                INSERT OR REPLACE INTO resolved_curriculum
                (leaid, grade_band, curriculum_normalized, status, confidence,
                 source_candidate_ids, resolution_method, resolved_date)
                VALUES (?, '68', ?, ?, ?, ?, 'single_source', ?)
            """, (leaid, g68_norm or g68_raw, status, confidence,
                  json.dumps([ec_id]), TODAY))
            rc_count += 1

    conn.commit()
    return ec_count, rc_count


def verify_migration(conn):
    """Print migration verification stats."""
    cur = conn.cursor()

    print("\n=== Migration Verification ===")

    cur.execute("SELECT COUNT(*) FROM curriculum")
    legacy = cur.fetchone()[0]
    print(f"Legacy curriculum rows: {legacy}")

    cur.execute("SELECT COUNT(*) FROM extraction_candidates")
    ec = cur.fetchone()[0]
    print(f"Extraction candidates: {ec}")

    cur.execute("SELECT grade_band, COUNT(*) FROM extraction_candidates GROUP BY grade_band")
    for band, cnt in cur.fetchall():
        print(f"  {band}: {cnt}")

    cur.execute("SELECT COUNT(*) FROM resolved_curriculum")
    rc = cur.fetchone()[0]
    print(f"Resolved curriculum entries: {rc}")

    cur.execute("SELECT status, COUNT(*) FROM resolved_curriculum GROUP BY status")
    for status, cnt in cur.fetchall():
        print(f"  {status}: {cnt}")

    cur.execute("SELECT COUNT(DISTINCT leaid) FROM resolved_curriculum")
    unique_leaids = cur.fetchone()[0]
    print(f"Unique LEAIDs in resolved: {unique_leaids}")

    cur.execute("SELECT COUNT(*) FROM source_registry")
    sr = cur.fetchone()[0]
    print(f"Source registry entries: {sr}")

    cur.execute("SELECT COUNT(*) FROM districts")
    total = cur.fetchone()[0]
    print(f"Total districts: {total}")
    print(f"Coverage: {unique_leaids}/{total} ({unique_leaids/total*100:.1f}%)")


def main():
    print(f"Database: {DB_PATH}")

    if not os.path.exists(DB_PATH):
        print("ERROR: Database not found. Run setup.py first.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    print("\n1. Creating new tables...")
    create_new_tables(conn)
    print("   Done.")

    print("\n2. Migrating legacy data...")
    result = migrate_legacy_data(conn)
    if result:
        ec_count, rc_count = result
        print(f"   Migrated: {ec_count} extraction candidates, {rc_count} resolved entries")
    else:
        print("   Skipped (already migrated)")

    # Log this run
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO run_logs (run_date, phase, status, notes)
        VALUES (?, 'migration', 'success', 'Schema migration to evidence-first model')
    """, (TODAY,))
    conn.commit()

    verify_migration(conn)
    conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    main()
