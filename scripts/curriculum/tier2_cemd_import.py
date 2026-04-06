"""Tier 2: Import CEMD Market Explorer data.

CEMD (Center for Education Market Dynamics) publishes district-level
curriculum adoption data for ~2,700 districts via their Market Explorer.

The data uses hashed LEA IDs, so we match districts by state + lat/lng
proximity to our NCES district centroids.

Usage:
    python scripts/curriculum/tier2_cemd_import.py
"""

import json
import math
import os
import sqlite3
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
from normalize import load_normalization_map, normalize_curriculum_name

DB_PATH = os.path.join(os.path.dirname(__file__), "curriculum.db")
CEMD_PATH = os.path.join(os.path.dirname(__file__), "seed_data", "cemd_districts.json")
CENTROIDS_PATH = os.path.join(os.path.dirname(__file__), "seed_data", "district_centroids.json")
SOURCE_TIER = 2
SOURCE_URL = "https://www.cemd.org/market-explorer/"
TODAY = date.today().isoformat()

# Maximum distance in km to consider a match
MAX_DISTANCE_KM = 15


def haversine_km(lat1, lon1, lat2, lon2):
    """Calculate distance between two points in km."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def load_cemd_data():
    """Load CEMD district data."""
    with open(CEMD_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_centroids():
    """Load our district centroids."""
    with open(CENTROIDS_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_state_index(centroids):
    """Index centroids by state for faster lookup."""
    index = {}
    for leaid, data in centroids.items():
        state = data["state"]
        if state not in index:
            index[state] = []
        index[state].append((leaid, data["lat"], data["lng"], data.get("name", ""), data.get("schools", 0)))
    return index


def match_cemd_to_nces(cemd_districts, state_index):
    """Match CEMD districts to NCES LEAIDs by state + lat/lng proximity.

    Returns list of (leaid, cemd_district) tuples.
    """
    matched = []
    unmatched = 0
    ambiguous = 0

    for cd in cemd_districts:
        state = cd["state"]
        if state not in state_index:
            unmatched += 1
            continue

        clat, clng = cd["lat"], cd["lng"]

        # Find closest district in same state
        best_dist = float("inf")
        best_leaid = None
        second_best = float("inf")

        for leaid, lat, lng, name, schools in state_index[state]:
            d = haversine_km(clat, clng, lat, lng)
            if d < best_dist:
                second_best = best_dist
                best_dist = d
                best_leaid = leaid
            elif d < second_best:
                second_best = d

        if best_dist > MAX_DISTANCE_KM:
            unmatched += 1
            continue

        # Check for ambiguity — if second-best is very close to best
        if second_best < best_dist * 1.5 and best_dist > 2:
            ambiguous += 1
            # Still use the match but note it

        matched.append((best_leaid, cd, best_dist))

    return matched, unmatched, ambiguous


def main():
    conn = sqlite3.connect(DB_PATH)
    mapping = load_normalization_map()

    # Load data
    cemd_districts = load_cemd_data()
    centroids = load_centroids()
    state_index = build_state_index(centroids)

    print(f"CEMD districts: {len(cemd_districts)}")
    print(f"Our districts with centroids: {len(centroids)}")

    # Filter to only those with math curriculum data
    with_math = [d for d in cemd_districts if d.get("k5") or d.get("g68")]
    print(f"CEMD districts with math data: {len(with_math)}")

    # Match
    print("\nMatching by state + lat/lng...")
    matched, unmatched, ambiguous = match_cemd_to_nces(with_math, state_index)
    print(f"  Matched: {len(matched)}")
    print(f"  Unmatched: {unmatched}")
    print(f"  Ambiguous (still used): {ambiguous}")

    # Distance stats
    distances = [d for _, _, d in matched]
    if distances:
        avg_dist = sum(distances) / len(distances)
        max_dist = max(distances)
        under_1km = sum(1 for d in distances if d < 1)
        under_5km = sum(1 for d in distances if d < 5)
        print(f"\nDistance stats:")
        print(f"  Avg: {avg_dist:.1f} km")
        print(f"  Max: {max_dist:.1f} km")
        print(f"  <1km: {under_1km} ({under_1km/len(distances)*100:.0f}%)")
        print(f"  <5km: {under_5km} ({under_5km/len(distances)*100:.0f}%)")

    # Check for existing tier 1 data — don't overwrite higher-quality sources
    cur = conn.cursor()
    cur.execute("SELECT leaid FROM curriculum WHERE source_tier = 1")
    tier1_leaids = set(row[0] for row in cur.fetchall())
    print(f"\nExisting Tier 1 districts: {len(tier1_leaids)}")

    # Insert
    inserted = 0
    skipped_tier1 = 0
    skipped_dup = 0
    seen_leaids = set()

    for leaid, cd, dist in matched:
        if leaid in tier1_leaids:
            skipped_tier1 += 1
            continue
        if leaid in seen_leaids:
            skipped_dup += 1
            continue
        seen_leaids.add(leaid)

        k5_raw = cd.get("k5")
        g68_raw = cd.get("g68")

        k5_norm = normalize_curriculum_name(k5_raw, mapping)[0] if k5_raw else None
        g68_norm = normalize_curriculum_name(g68_raw, mapping)[0] if g68_raw else None

        # Confidence based on distance
        if dist < 2:
            confidence = 0.9
        elif dist < 5:
            confidence = 0.8
        elif dist < 10:
            confidence = 0.7
        else:
            confidence = 0.6

        cur.execute("""
            INSERT OR REPLACE INTO curriculum
            (leaid, k5_curriculum, k5_normalized, grade68_curriculum, grade68_normalized,
             source_tier, source_url, confidence, date_collected)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            leaid, k5_raw, k5_norm, g68_raw, g68_norm,
            SOURCE_TIER, SOURCE_URL, confidence, TODAY,
        ))
        inserted += 1

    conn.commit()

    print(f"\nResults:")
    print(f"  Inserted: {inserted}")
    print(f"  Skipped (Tier 1 exists): {skipped_tier1}")
    print(f"  Skipped (duplicate LEAID): {skipped_dup}")

    # Overall stats
    cur.execute("SELECT COUNT(*) FROM curriculum")
    total = cur.fetchone()[0]
    cur.execute("SELECT source_tier, COUNT(*) FROM curriculum GROUP BY source_tier ORDER BY source_tier")
    by_tier = cur.fetchall()
    print(f"\n  Total districts with data: {total}")
    for tier, count in by_tier:
        print(f"    Tier {tier}: {count}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
