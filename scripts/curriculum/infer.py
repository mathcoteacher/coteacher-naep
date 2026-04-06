"""Phase 5: Inference engine for full district coverage.

For districts without direct evidence, infers curriculum using:
1. State-level mode (most common curriculum in the state from verified data)
2. Geographic peer matching (nearest districts with known curriculum)
3. Size-band peer matching (similar-sized districts in same state)

Every inferred value is labeled with status='inferred', method, and confidence.

Usage:
    python scripts/curriculum/infer.py
"""

import json
import math
import os
import sqlite3
import sys
from collections import Counter
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

DB_PATH = os.path.join(os.path.dirname(__file__), "curriculum.db")
CENTROIDS_PATH = os.path.join(os.path.dirname(__file__), "seed_data", "district_centroids.json")
TODAY = date.today().isoformat()


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def build_state_curriculum_modes(conn):
    """For each state + grade_band, compute the most common curriculum
    from verified/high-confidence resolved data.

    Returns dict: {(state, grade_band): [(curriculum, count, pct), ...]}
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT d.state, rc.grade_band, rc.curriculum_normalized, COUNT(*) as cnt
        FROM resolved_curriculum rc
        JOIN districts d ON rc.leaid = d.leaid
        WHERE rc.status = 'verified' OR rc.confidence >= 0.6
        GROUP BY d.state, rc.grade_band, rc.curriculum_normalized
        ORDER BY d.state, rc.grade_band, cnt DESC
    """)

    state_modes = {}
    for state, band, curriculum, cnt in cur.fetchall():
        key = (state, band)
        if key not in state_modes:
            state_modes[key] = []
        state_modes[key].append((curriculum, cnt))

    # Add percentages
    for key in state_modes:
        total = sum(cnt for _, cnt in state_modes[key])
        state_modes[key] = [
            (name, cnt, round(cnt / total * 100, 1))
            for name, cnt in state_modes[key]
        ]

    return state_modes


def build_geographic_index(conn, centroids):
    """Build geographic index of districts with known curriculum.

    Returns dict: {leaid: (lat, lng, state, {grade_band: curriculum})}
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT rc.leaid, rc.grade_band, rc.curriculum_normalized
        FROM resolved_curriculum rc
        WHERE rc.confidence >= 0.5
    """)

    known = {}
    for leaid, band, curriculum in cur.fetchall():
        if leaid not in known:
            known[leaid] = {}
        known[leaid][band] = curriculum

    # Merge with centroids
    index = {}
    for leaid, currs in known.items():
        if leaid in centroids:
            c = centroids[leaid]
            index[leaid] = (c["lat"], c["lng"], c["state"], currs)

    return index


def find_nearest_peers(target_leaid, target_lat, target_lng, target_state,
                       geo_index, grade_band, n=5, max_km=100):
    """Find the N nearest districts in the same state with known curriculum
    for the given grade band.

    Returns list of (leaid, curriculum, distance_km).
    """
    peers = []
    for leaid, (lat, lng, state, currs) in geo_index.items():
        if leaid == target_leaid:
            continue
        if state != target_state:
            continue
        if grade_band not in currs:
            continue

        dist = haversine_km(target_lat, target_lng, lat, lng)
        if dist <= max_km:
            peers.append((leaid, currs[grade_band], dist))

    peers.sort(key=lambda x: x[2])
    return peers[:n]


def infer_from_peers(peers):
    """Given a list of peer (leaid, curriculum, distance) tuples,
    infer the most likely curriculum.

    Weights closer peers more heavily.
    Returns (curriculum, confidence) or (None, 0).
    """
    if not peers:
        return None, 0

    # Weight by inverse distance (closer = more weight)
    weighted = Counter()
    for _, curriculum, dist in peers:
        weight = 1.0 / max(dist, 0.5)  # Avoid div by zero
        weighted[curriculum] += weight

    if not weighted:
        return None, 0

    best = weighted.most_common(1)[0]
    total_weight = sum(weighted.values())
    dominance = best[1] / total_weight  # How dominant is the top choice

    # Confidence based on dominance and number of peers
    confidence = min(0.5, dominance * 0.4 * min(len(peers) / 3, 1.0))

    return best[0], round(confidence, 3)


def infer_from_state_mode(state, grade_band, state_modes):
    """Infer curriculum from the state's most common curriculum.

    Returns (curriculum, confidence) or (None, 0).
    """
    key = (state, grade_band)
    if key not in state_modes or not state_modes[key]:
        return None, 0

    top = state_modes[key][0]
    curriculum, count, pct = top

    # Confidence based on how dominant the top curriculum is
    if pct >= 50:
        confidence = 0.4
    elif pct >= 30:
        confidence = 0.3
    elif pct >= 15:
        confidence = 0.25
    else:
        confidence = 0.2

    return curriculum, confidence


def run_inference(conn, centroids):
    """Run inference for all districts missing K-5 and/or 6-8 data.

    Strategy per missing (leaid, grade_band):
    1. Try geographic peer inference (nearest 5 in same state)
    2. Fall back to state-level mode
    3. Fall back to national mode if state has no data
    """
    cur = conn.cursor()

    # Build indices
    print("  Building state curriculum modes...")
    state_modes = build_state_curriculum_modes(conn)

    print("  Building geographic index...")
    geo_index = build_geographic_index(conn, centroids)
    print(f"  Geo index: {len(geo_index)} districts with location + curriculum")

    # Compute national modes as fallback
    national_modes = {}
    for (state, band), entries in state_modes.items():
        if band not in national_modes:
            national_modes[band] = Counter()
        for name, cnt, _ in entries:
            national_modes[band][name] += cnt

    # Get all districts
    cur.execute("SELECT leaid, district_name, state, school_count FROM districts")
    all_districts = cur.fetchall()

    # Get existing resolved data
    cur.execute("SELECT leaid, grade_band FROM resolved_curriculum")
    existing = set((row[0], row[1]) for row in cur.fetchall())

    # Find what's missing
    missing = []
    for leaid, name, state, schools in all_districts:
        for band in ("k5", "68"):
            if (leaid, band) not in existing:
                missing.append((leaid, name, state, schools, band))

    print(f"\n  Missing entries to infer: {len(missing)}")

    # Run inference
    inferred_peer = 0
    inferred_state = 0
    inferred_national = 0
    failed = 0

    for leaid, name, state, schools, band in missing:
        curriculum = None
        confidence = 0
        method = None

        # Strategy 1: Geographic peer inference
        if leaid in centroids:
            c = centroids[leaid]
            peers = find_nearest_peers(
                leaid, c["lat"], c["lng"], state, geo_index, band, n=5, max_km=100
            )
            if peers:
                curriculum, confidence = infer_from_peers(peers)
                if curriculum:
                    method = "geographic_peer"

        # Strategy 2: State-level mode
        if not curriculum:
            curriculum, confidence = infer_from_state_mode(state, band, state_modes)
            if curriculum:
                method = "state_mode"

        # Strategy 3: National mode
        if not curriculum and band in national_modes:
            top = national_modes[band].most_common(1)
            if top:
                curriculum = top[0][0]
                confidence = 0.15
                method = "national_mode"

        if curriculum:
            # Insert extraction candidate
            cur.execute("""
                INSERT INTO extraction_candidates
                (leaid, grade_band, curriculum_raw, curriculum_normalized,
                 source_type, confidence, extraction_method, date_collected)
                VALUES (?, ?, ?, ?, 'inference', ?, ?, ?)
            """, (leaid, band, curriculum, curriculum,
                  confidence, method, TODAY))
            ec_id = cur.lastrowid

            # Insert resolved entry
            cur.execute("""
                INSERT OR REPLACE INTO resolved_curriculum
                (leaid, grade_band, curriculum_normalized, status, confidence,
                 source_candidate_ids, resolution_method, resolved_date)
                VALUES (?, ?, ?, 'inferred', ?, ?, 'inference', ?)
            """, (leaid, band, curriculum, confidence,
                  json.dumps([ec_id]), TODAY))

            if method == "geographic_peer":
                inferred_peer += 1
            elif method == "state_mode":
                inferred_state += 1
            else:
                inferred_national += 1
        else:
            failed += 1

    conn.commit()

    return {
        "peer": inferred_peer,
        "state": inferred_state,
        "national": inferred_national,
        "failed": failed,
        "total": inferred_peer + inferred_state + inferred_national,
    }


def main():
    print(f"Database: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)

    # Load centroids
    print("\nLoading district centroids...")
    with open(CENTROIDS_PATH, encoding="utf-8") as f:
        centroids = json.load(f)
    print(f"  Centroids loaded: {len(centroids)}")

    print("\nRunning inference...")
    results = run_inference(conn, centroids)

    print(f"\n=== Inference Results ===")
    print(f"  Geographic peer: {results['peer']}")
    print(f"  State mode: {results['state']}")
    print(f"  National mode: {results['national']}")
    print(f"  Failed: {results['failed']}")
    print(f"  Total inferred: {results['total']}")

    # Verify coverage
    cur = conn.cursor()
    cur.execute("SELECT COUNT(DISTINCT leaid) FROM resolved_curriculum")
    covered = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM districts")
    total = cur.fetchone()[0]
    print(f"\n  Coverage: {covered}/{total} ({covered/total*100:.1f}%)")

    cur.execute("""
        SELECT COUNT(*) FROM (
            SELECT leaid FROM resolved_curriculum WHERE grade_band = 'k5'
            INTERSECT
            SELECT leaid FROM resolved_curriculum WHERE grade_band = '68'
        )
    """)
    both = cur.fetchone()[0]
    print(f"  Districts with both bands: {both}")

    cur.execute("SELECT status, COUNT(*) FROM resolved_curriculum GROUP BY status")
    for status, cnt in cur.fetchall():
        print(f"  {status}: {cnt}")

    # Log run
    cur.execute("""
        INSERT INTO run_logs (run_date, phase, status, districts_found, notes)
        VALUES (?, 'inference', 'success', ?, ?)
    """, (TODAY, results['total'],
          f"peer={results['peer']}, state={results['state']}, national={results['national']}"))
    conn.commit()

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
