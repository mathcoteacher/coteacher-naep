"""Tier 1: Massachusetts curriculum data from curriculumdashboard.mass.gov

The MA DESE Curriculum Dashboard is a Next.js SSG app. All data is embedded
in the page as __NEXT_DATA__ and also available via the Next.js data route:
  /_next/data/{buildId}/explore.json

Data structure:
  - districtData: array of district info (389 districts, Org Code, demographics)
  - selections: 13,744 records mapping schools to curricula with grade ranges
  - curriculumData: 171 curriculum products with EdReports ratings

We extract Math selections for K-5 and 6-8 grade bands, match to our LEAID
via MA district codes, and insert into the curriculum table.

Usage:
    python scripts/curriculum/tier1_massachusetts.py
"""

import json
import os
import re
import sqlite3
import ssl
import sys
import urllib.request
from collections import defaultdict
from datetime import date

# Add parent dir to path for normalize import
sys.path.insert(0, os.path.dirname(__file__))
from normalize import load_normalization_map, normalize_curriculum_name

DB_PATH = os.path.join(os.path.dirname(__file__), "curriculum.db")
SOURCE_URL = "https://curriculumdashboard.mass.gov/explore"
SOURCE_TIER = 1
TODAY = date.today().isoformat()


def fetch_page_data():
    """Fetch the explore page and extract __NEXT_DATA__ JSON."""
    print("Fetching MA curriculum dashboard page...")
    req = urllib.request.Request(
        SOURCE_URL,
        headers={"User-Agent": "Mozilla/5.0 (curriculum-research)"},
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, context=ctx) as resp:
        html = resp.read().decode("utf-8")

    # Extract __NEXT_DATA__ from the HTML
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not match:
        raise RuntimeError("Could not find __NEXT_DATA__ in page HTML")

    data = json.loads(match.group(1))
    page_props = data["props"]["pageProps"]
    print(f"  Found {len(page_props['districtData'])} districts")
    print(f"  Found {len(page_props['selections'])} curriculum selections")
    print(f"  Found {len(page_props['curriculumData'])} curriculum products")
    return page_props


def build_ma_district_lookup(conn):
    """Build a lookup from MA district name -> LEAID for matching.

    MA uses its own district codes (IM_ParentOrgCode), not NCES LEAIDs.
    We'll need to match by district name to our districts table.
    """
    cur = conn.cursor()
    cur.execute("SELECT leaid, district_name FROM districts WHERE state = 'MA'")
    rows = cur.fetchall()

    # Build name -> leaid mapping (lowercase for fuzzy matching)
    lookup = {}
    for leaid, name in rows:
        lookup[name.lower().strip()] = leaid
        # Also index without common suffixes
        for suffix in [" school district", " public schools", " public school district",
                       " regional school district", " district", " schools"]:
            if name.lower().endswith(suffix):
                lookup[name.lower().replace(suffix, "").strip()] = leaid

    return lookup, {leaid: name for leaid, name in rows}


def match_district(ma_name, lookup):
    """Try to match a MA district name to our LEAID.

    Returns (leaid, match_quality) or (None, None).
    """
    name_lower = ma_name.lower().strip()

    # Direct match
    if name_lower in lookup:
        return lookup[name_lower], "exact"

    # Try removing "(District)" suffix that MA uses
    cleaned = re.sub(r"\s*\(district\)\s*$", "", name_lower, flags=re.IGNORECASE).strip()
    if cleaned in lookup:
        return lookup[cleaned], "cleaned"

    # Try without "public" or "regional"
    for word in ["public ", "regional "]:
        variant = cleaned.replace(word, "")
        if variant in lookup:
            return lookup[variant], "simplified"

    # Substring match — if our district name is contained in the MA name
    for key, leaid in lookup.items():
        if key in name_lower or name_lower in key:
            if len(key) > 3:  # Avoid tiny matches
                return leaid, "substring"

    return None, None


def extract_math_curriculum_by_district(page_props):
    """Extract K-5 and 6-8 math curriculum selections grouped by MA district code.

    Returns dict: ma_parent_org_code -> {
        'name': district_name,
        'k5': [(product_name, publisher), ...],
        'g68': [(product_name, publisher), ...],
    }
    """
    selections = page_props["selections"]
    district_data = page_props["districtData"]

    # Build MA org code -> district name lookup
    org_name = {}
    for d in district_data:
        code = d.get("District Code") or d.get("Org Code")
        name = d.get("District Name") or d.get("Org Name", "")
        if code:
            org_name[code] = name

    # Group math selections by parent district
    districts = defaultdict(lambda: {"name": "", "k5": [], "g68": []})

    for sel in selections:
        if sel.get("SA_SubjectName") != "Mathematics":
            continue

        parent_code = sel.get("IM_ParentOrgCode")
        if not parent_code:
            continue

        begin_grade = sel.get("IM_BeginGradeCode", 99)
        end_grade = sel.get("IM_EndGradeCode", 0)
        product = sel.get("PRD_ProductName", "").strip()
        publisher = sel.get("PUB_PublisherName", "").strip()

        # Skip "Other" with no useful info
        if product == "Other" and not publisher:
            continue

        districts[parent_code]["name"] = org_name.get(parent_code, f"MA-{parent_code}")

        # Classify into K-5 or 6-8
        # K-5: any selection that covers grades K-5 (begin <= 5)
        # 6-8: any selection that covers grades 6-8 (begin >= 6, end <= 8)
        if begin_grade <= 5 and end_grade <= 8:
            districts[parent_code]["k5"].append((product, publisher))
        if begin_grade >= 6 and end_grade <= 8:
            districts[parent_code]["g68"].append((product, publisher))

    return dict(districts)


def pick_primary_curriculum(selections, mapping):
    """Pick the primary curriculum from a list of (product, publisher) tuples.

    Prefers known/normalizable curricula. If multiple, joins them.
    Returns (raw_name, normalized_name).
    """
    if not selections:
        return None, None

    # Deduplicate
    seen = set()
    unique = []
    for product, publisher in selections:
        key = (product.lower(), publisher.lower())
        if key not in seen:
            seen.add(key)
            unique.append((product, publisher))

    # For "Other" entries, try using publisher name
    resolved = []
    for product, publisher in unique:
        if product == "Other" and publisher:
            resolved.append(publisher)
        else:
            resolved.append(product)

    # If only one curriculum, use it
    if len(resolved) == 1:
        raw = resolved[0]
        normalized, _ = normalize_curriculum_name(raw, mapping)
        return raw, normalized

    # Multiple curricula — try to find the most common/known one
    for raw in resolved:
        normalized, matched = normalize_curriculum_name(raw, mapping)
        if matched:
            return raw, normalized

    # Fall back to joining them
    raw = " / ".join(resolved[:3])
    normalized, _ = normalize_curriculum_name(raw, mapping)
    return raw, normalized


def main():
    conn = sqlite3.connect(DB_PATH)
    mapping = load_normalization_map()

    # Fetch data
    page_props = fetch_page_data()

    # Build district matching lookup
    ma_lookup, ma_districts = build_ma_district_lookup(conn)
    print(f"\nOur MA districts in DB: {len(ma_districts)}")

    # Extract math curriculum by MA district
    math_data = extract_math_curriculum_by_district(page_props)
    print(f"MA districts with math data: {len(math_data)}")

    # Match and insert
    matched = 0
    unmatched = []
    inserted = 0
    cur = conn.cursor()

    for ma_code, info in sorted(math_data.items(), key=lambda x: x[1]["name"]):
        ma_name = info["name"]
        leaid, quality = match_district(ma_name, ma_lookup)

        if not leaid:
            unmatched.append(ma_name)
            continue

        matched += 1

        k5_raw, k5_norm = pick_primary_curriculum(info["k5"], mapping)
        g68_raw, g68_norm = pick_primary_curriculum(info["g68"], mapping)

        if not k5_raw and not g68_raw:
            continue

        cur.execute("""
            INSERT OR REPLACE INTO curriculum
            (leaid, k5_curriculum, k5_normalized, grade68_curriculum, grade68_normalized,
             source_tier, source_url, confidence, date_collected)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            leaid, k5_raw, k5_norm, g68_raw, g68_norm,
            SOURCE_TIER, SOURCE_URL, 1.0, TODAY,
        ))
        inserted += 1

    conn.commit()

    print(f"\nResults:")
    print(f"  Matched: {matched}")
    print(f"  Inserted: {inserted}")
    print(f"  Unmatched: {len(unmatched)}")
    if unmatched[:10]:
        print(f"  Sample unmatched: {unmatched[:10]}")

    # Summary report
    print(f"\nCurriculum distribution (K-5):")
    cur.execute("""
        SELECT k5_normalized, COUNT(*) as cnt
        FROM curriculum WHERE source_tier = 1 AND k5_normalized IS NOT NULL
        GROUP BY k5_normalized ORDER BY cnt DESC LIMIT 15
    """)
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]} districts")

    print(f"\nCurriculum distribution (6-8):")
    cur.execute("""
        SELECT grade68_normalized, COUNT(*) as cnt
        FROM curriculum WHERE source_tier = 1 AND grade68_normalized IS NOT NULL
        GROUP BY grade68_normalized ORDER BY cnt DESC LIMIT 15
    """)
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]} districts")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
