"""Phase 0: Create SQLite database and populate districts table from intermediate files.

Reads scripts/intermediate/{STATE}.json files, extracts LEAID (first 7 digits of ncessch),
and builds the districts and curriculum tables.

Usage:
    python scripts/curriculum/setup.py
"""

import json
import os
import sqlite3
import sys
from collections import defaultdict

from normalize import seed_curriculum_names_table

INTERMEDIATE_DIR = os.path.join(os.path.dirname(__file__), "..", "intermediate")
DB_PATH = os.path.join(os.path.dirname(__file__), "curriculum.db")


def create_tables(conn):
    """Create the database schema."""
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS districts (
            leaid TEXT PRIMARY KEY,
            district_name TEXT NOT NULL,
            state TEXT NOT NULL,
            school_count INTEGER
        );

        CREATE TABLE IF NOT EXISTS curriculum (
            leaid TEXT PRIMARY KEY,
            k5_curriculum TEXT,
            k5_normalized TEXT,
            grade68_curriculum TEXT,
            grade68_normalized TEXT,
            source_tier INTEGER NOT NULL,
            source_url TEXT,
            confidence REAL DEFAULT 1.0,
            date_collected TEXT NOT NULL,
            FOREIGN KEY (leaid) REFERENCES districts(leaid)
        );

        CREATE TABLE IF NOT EXISTS curriculum_names (
            raw_name TEXT PRIMARY KEY,
            normalized_name TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_districts_state ON districts(state);
        CREATE INDEX IF NOT EXISTS idx_curriculum_tier ON curriculum(source_tier);
    """)
    conn.commit()


def extract_districts(intermediate_dir):
    """Extract district information from intermediate JSON files.

    Groups schools by LEAID (first 7 digits of ncessch) to build the districts table.
    Uses the district name from the intermediate file's districts array when possible,
    falling back to the most common district name from school records.
    """
    districts = {}  # leaid -> {name, state, school_count}

    for filename in sorted(os.listdir(intermediate_dir)):
        if not filename.endswith(".json"):
            continue

        filepath = os.path.join(intermediate_dir, filename)
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)

        state = data["state"]

        # Build a lookup of district names from the districts array
        district_names = {}
        for d in data.get("districts", []):
            district_names[d["name"]] = d.get("schoolCount", 0)

        # Group schools by LEAID
        leaid_schools = defaultdict(list)
        for school in data.get("schools", []):
            ncessch = school.get("ncessch", "")
            if not ncessch or len(ncessch) < 7:
                continue
            leaid = ncessch[:7]
            leaid_schools[leaid].append(school)

        # Build district records
        for leaid, schools in leaid_schools.items():
            # Try to get district name from the most common district field in schools
            name_counts = defaultdict(int)
            for s in schools:
                dname = s.get("district", "")
                if dname:
                    name_counts[dname] += 1

            if name_counts:
                district_name = max(name_counts, key=name_counts.get)
            else:
                district_name = f"Unknown District ({leaid})"

            districts[leaid] = {
                "name": district_name,
                "state": state,
                "school_count": len(schools),
            }

    return districts


def populate_districts(conn, districts):
    """Insert district records into the database."""
    cur = conn.cursor()
    cur.execute("DELETE FROM districts")
    for leaid, info in districts.items():
        cur.execute(
            "INSERT INTO districts (leaid, district_name, state, school_count) VALUES (?, ?, ?, ?)",
            (leaid, info["name"], info["state"], info["school_count"]),
        )
    conn.commit()
    return len(districts)


def verify(conn):
    """Run verification queries and print results."""
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM districts")
    total = cur.fetchone()[0]
    print(f"\nTotal districts: {total}")

    cur.execute("SELECT state, COUNT(*) as cnt FROM districts GROUP BY state ORDER BY cnt DESC LIMIT 10")
    print("\nTop 10 states by district count:")
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]}")

    cur.execute("SELECT state, COUNT(*) as cnt FROM districts GROUP BY state ORDER BY cnt ASC LIMIT 5")
    print("\nBottom 5 states by district count:")
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]}")

    # Spot-check 5 districts across different states
    cur.execute("""
        SELECT leaid, district_name, state, school_count FROM districts
        WHERE state IN ('CA', 'TX', 'NY', 'RI', 'NE')
        ORDER BY school_count DESC
        LIMIT 5
    """)
    print("\nSpot-check (largest district per state):")
    for row in cur.fetchall():
        print(f"  LEAID={row[0]} | {row[1]} ({row[2]}) | {row[3]} schools")


def main():
    print(f"Database path: {DB_PATH}")
    print(f"Intermediate dir: {os.path.abspath(INTERMEDIATE_DIR)}")

    # Check intermediate dir exists
    if not os.path.isdir(INTERMEDIATE_DIR):
        print(f"ERROR: Intermediate directory not found: {INTERMEDIATE_DIR}")
        sys.exit(1)

    # Create/reset database
    conn = sqlite3.connect(DB_PATH)
    create_tables(conn)

    # Extract and populate districts
    print("\nExtracting districts from intermediate files...")
    districts = extract_districts(INTERMEDIATE_DIR)
    count = populate_districts(conn, districts)
    print(f"Populated {count} districts")

    # Seed curriculum names
    print("\nSeeding curriculum name mappings...")
    seed_curriculum_names_table(DB_PATH)

    # Verify
    verify(conn)
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
