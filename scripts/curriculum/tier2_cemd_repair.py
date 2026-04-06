"""Phase 4: Repair CEMD matching with optimal one-to-one assignment.

The original tier2_cemd_import.py used first-seen duplicate suppression,
which can assign the wrong CEMD district when multiple are close to the
same NCES centroid. This uses scipy's linear_sum_assignment for globally
optimal matching.

Also adds proper evidence entries to extraction_candidates (not just the
legacy curriculum table).

Usage:
    python scripts/curriculum/tier2_cemd_repair.py
"""

import json
import math
import os
import sqlite3
import sys
from datetime import date

import numpy as np
from scipy.optimize import linear_sum_assignment

sys.path.insert(0, os.path.dirname(__file__))
from normalize import load_normalization_map, normalize_curriculum_name

DB_PATH = os.path.join(os.path.dirname(__file__), "curriculum.db")
CEMD_PATH = os.path.join(os.path.dirname(__file__), "seed_data", "cemd_districts.json")
CENTROIDS_PATH = os.path.join(os.path.dirname(__file__), "seed_data", "district_centroids.json")
TODAY = date.today().isoformat()

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


def optimal_match_state(cemd_state, nces_state):
    """Compute optimal one-to-one matching for districts in a single state.

    Uses the Hungarian algorithm (linear_sum_assignment) for globally
    optimal assignment that minimizes total distance.

    Returns list of (nces_leaid, cemd_idx, distance, ambiguity_flag).
    """
    if not cemd_state or not nces_state:
        return []

    n_cemd = len(cemd_state)
    n_nces = len(nces_state)

    # Build cost matrix: cemd_i x nces_j = distance in km
    # Use a large penalty for distances beyond MAX_DISTANCE_KM
    PENALTY = 1e6
    cost = np.full((n_cemd, n_nces), PENALTY, dtype=np.float64)

    for i, cd in enumerate(cemd_state):
        for j, (leaid, lat, lng, name, schools) in enumerate(nces_state):
            d = haversine_km(cd["lat"], cd["lng"], lat, lng)
            if d <= MAX_DISTANCE_KM:
                cost[i, j] = d

    # Solve assignment problem
    row_ind, col_ind = linear_sum_assignment(cost)

    matches = []
    for i, j in zip(row_ind, col_ind):
        dist = cost[i, j]
        if dist >= PENALTY:
            continue  # No valid match

        leaid = nces_state[j][0]

        # Check ambiguity: is the second-best assignment close?
        row_costs = sorted(cost[i])
        ambiguous = False
        if len(row_costs) > 1 and row_costs[0] > 2:
            if row_costs[1] < row_costs[0] * 1.5:
                ambiguous = True

        matches.append((leaid, i, dist, ambiguous))

    return matches


def main():
    conn = sqlite3.connect(DB_PATH)
    mapping = load_normalization_map()
    cur = conn.cursor()

    # Load data
    with open(CEMD_PATH, encoding="utf-8") as f:
        cemd_districts = json.load(f)
    with open(CENTROIDS_PATH, encoding="utf-8") as f:
        centroids = json.load(f)

    # Filter CEMD to those with math data
    with_math = [d for d in cemd_districts if d.get("k5") or d.get("g68")]
    print(f"CEMD districts with math data: {len(with_math)}")

    # Group by state
    cemd_by_state = {}
    for cd in with_math:
        state = cd["state"]
        if state not in cemd_by_state:
            cemd_by_state[state] = []
        cemd_by_state[state].append(cd)

    nces_by_state = {}
    for leaid, data in centroids.items():
        state = data["state"]
        if state not in nces_by_state:
            nces_by_state[state] = []
        nces_by_state[state].append(
            (leaid, data["lat"], data["lng"], data.get("name", ""), data.get("schools", 0))
        )

    print(f"States with CEMD data: {len(cemd_by_state)}")

    # Remove old CEMD extraction candidates to replace with improved matches
    cur.execute("SELECT COUNT(*) FROM extraction_candidates WHERE source_type = 'cemd'")
    old_cemd_count = cur.fetchone()[0]
    cur.execute("DELETE FROM extraction_candidates WHERE source_type = 'cemd'")
    print(f"Removed {old_cemd_count} old CEMD extraction candidates")

    # Run optimal matching per state
    total_matched = 0
    total_ambiguous = 0
    total_inserted = 0
    distances = []

    for state in sorted(cemd_by_state.keys()):
        cemd_state = cemd_by_state[state]
        nces_state = nces_by_state.get(state, [])

        if not nces_state:
            continue

        matches = optimal_match_state(cemd_state, nces_state)
        total_matched += len(matches)
        total_ambiguous += sum(1 for _, _, _, amb in matches if amb)

        for leaid, cemd_idx, dist, ambiguous in matches:
            cd = cemd_state[cemd_idx]
            distances.append(dist)

            # Distance-based confidence
            if dist < 1:
                confidence = 0.95
            elif dist < 2:
                confidence = 0.9
            elif dist < 5:
                confidence = 0.8
            elif dist < 10:
                confidence = 0.7
            else:
                confidence = 0.6

            # Downgrade for ambiguous matches
            if ambiguous:
                confidence *= 0.8

            k5_raw = cd.get("k5")
            g68_raw = cd.get("g68")

            # Insert K-5 evidence
            if k5_raw:
                k5_norm = normalize_curriculum_name(k5_raw, mapping)[0]
                cur.execute("""
                    INSERT INTO extraction_candidates
                    (leaid, grade_band, curriculum_raw, curriculum_normalized,
                     source_type, source_url, confidence, extraction_method, date_collected)
                    VALUES (?, 'k5', ?, ?, 'cemd', 'https://www.cemd.org/market-explorer/',
                            ?, 'geospatial', ?)
                """, (leaid, k5_raw, k5_norm, round(confidence, 3), TODAY))
                total_inserted += 1

            # Insert 6-8 evidence
            if g68_raw:
                g68_norm = normalize_curriculum_name(g68_raw, mapping)[0]
                cur.execute("""
                    INSERT INTO extraction_candidates
                    (leaid, grade_band, curriculum_raw, curriculum_normalized,
                     source_type, source_url, confidence, extraction_method, date_collected)
                    VALUES (?, '68', ?, ?, 'cemd', 'https://www.cemd.org/market-explorer/',
                            ?, 'geospatial', ?)
                """, (leaid, g68_raw, g68_norm, round(confidence, 3), TODAY))
                total_inserted += 1

    conn.commit()

    # Stats
    print(f"\n=== Optimal Matching Results ===")
    print(f"Total matched: {total_matched}")
    print(f"Ambiguous: {total_ambiguous}")
    print(f"Evidence items inserted: {total_inserted}")

    if distances:
        avg_dist = sum(distances) / len(distances)
        print(f"\nDistance stats:")
        print(f"  Avg: {avg_dist:.2f} km")
        print(f"  Max: {max(distances):.2f} km")
        print(f"  <1km: {sum(1 for d in distances if d < 1)} ({sum(1 for d in distances if d < 1)/len(distances)*100:.0f}%)")
        print(f"  <5km: {sum(1 for d in distances if d < 5)} ({sum(1 for d in distances if d < 5)/len(distances)*100:.0f}%)")

    # Log run
    cur.execute("""
        INSERT INTO run_logs (run_date, phase, status, districts_found, notes)
        VALUES (?, 'cemd_repair', 'success', ?, ?)
    """, (TODAY, total_matched, f"optimal matching, ambiguous={total_ambiguous}"))
    conn.commit()

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
