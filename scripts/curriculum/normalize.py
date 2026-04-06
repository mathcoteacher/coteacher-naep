"""Curriculum name normalization utilities.

Maps variant curriculum names (different publishers, editions, abbreviations)
to canonical names for consistent grouping and analysis.
"""

import csv
import os
import re
import sqlite3

SEED_FILE = os.path.join(os.path.dirname(__file__), "seed_data", "curriculum_names.csv")


def load_normalization_map(seed_file=SEED_FILE):
    """Load the raw_name -> normalized_name mapping from seed CSV."""
    mapping = {}
    with open(seed_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = row["raw_name"].strip()
            normalized = row["normalized_name"].strip()
            mapping[raw.lower()] = normalized
    return mapping


def normalize_curriculum_name(raw_name, mapping=None):
    """Normalize a curriculum name to its canonical form.

    Tries exact match (case-insensitive) first, then fuzzy substring matching
    against known curriculum names.

    Returns (normalized_name, matched) where matched=True if a mapping was found.
    """
    if not raw_name or raw_name.strip() == "":
        return (None, False)

    if mapping is None:
        mapping = load_normalization_map()

    raw_lower = raw_name.strip().lower()

    # Exact match
    if raw_lower in mapping:
        return (mapping[raw_lower], True)

    # Try removing parenthetical year/publisher info: "Eureka Math2 (Great Minds) 2021" -> "Eureka Math2"
    stripped = re.sub(r"\s*\(.*?\)\s*", " ", raw_name).strip()
    stripped = re.sub(r"\s*\d{4}\s*$", "", stripped).strip()
    stripped_lower = stripped.lower()
    if stripped_lower in mapping:
        return (mapping[stripped_lower], True)

    # Substring match: check if any known name is contained in the raw name
    for known_lower, canonical in sorted(mapping.items(), key=lambda x: -len(x[0])):
        if known_lower in raw_lower:
            return (canonical, True)

    return (raw_name.strip(), False)


def seed_curriculum_names_table(db_path):
    """Populate the curriculum_names table from the seed CSV."""
    mapping = load_normalization_map()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM curriculum_names")
    for raw_lower, normalized in mapping.items():
        cur.execute(
            "INSERT OR REPLACE INTO curriculum_names (raw_name, normalized_name) VALUES (?, ?)",
            (raw_lower, normalized),
        )
    conn.commit()
    conn.close()
    print(f"Seeded {len(mapping)} curriculum name mappings")


if __name__ == "__main__":
    # Quick test
    mapping = load_normalization_map()
    test_cases = [
        "Eureka Math2 (Great Minds) 2021",
        "Kendall Hunt Illustrative Math 2019",
        "enVision Mathematics Common Core",
        "Some Unknown Curriculum",
        "Big Ideas Math: A Common Core Curriculum - Algebra 1, Geometry, Algebra 2",
        "Bridges in Mathematics",
    ]
    for name in test_cases:
        normalized, matched = normalize_curriculum_name(name, mapping)
        status = "MATCHED" if matched else "UNMATCHED"
        print(f"  {status}: '{name}' -> '{normalized}'")
