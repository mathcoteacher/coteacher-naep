#!/usr/bin/env python3
"""
Extract school-level 8th grade math proficiency for all 50 states + DC
from the SQLite database, and output intermediate JSON files.

Reads:
  - SQLite database with achievement, schools, districts, states tables
  - NAEP JSON for state-level headline stats

Outputs:
  - scripts/intermediate/{STATE}.json with lat/lng (not yet projected)

Then run prepare-map-data.js to project to AlbersUSA pixel coordinates.
"""

import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DB_PATH = "/Volumes/SignatureMi/ohio_education_data/data/ohio_education.db"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NAEP_FILE = os.path.join(SCRIPT_DIR, "..", "public", "naep.json")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "intermediate")

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming"
}


def load_naep():
    with open(NAEP_FILE) as f:
        return json.load(f)


def extract_state(conn, state_code, naep_data):
    """Extract grade 8 math proficiency for one state, using most recent year."""

    # Find the most recent year with grade 8 math data for this state
    cur = conn.execute("""
        SELECT MAX(a.year)
        FROM states s
        JOIN districts d ON s.state_id = d.state_id
        JOIN schools sc ON d.district_id = sc.district_id
        JOIN achievement a ON sc.school_id = a.school_id
        WHERE s.state_code = ? AND a.subject = 'math' AND a.grade = '8'
          AND a.proficiency_rate IS NOT NULL
    """, (state_code,))

    row = cur.fetchone()
    if not row or not row[0]:
        return None

    year = row[0]

    # Fetch all schools with grade 8 math data for that year
    cur = conn.execute("""
        SELECT sc.name, sc.latitude, sc.longitude, sc.city,
               d.name as district_name, a.proficiency_rate, sc.nces_id
        FROM schools sc
        JOIN achievement a ON sc.school_id = a.school_id
        JOIN districts d ON sc.district_id = d.district_id
        JOIN states s ON d.state_id = s.state_id
        WHERE s.state_code = ? AND a.subject = 'math' AND a.grade = '8'
          AND a.year = ? AND a.proficiency_rate IS NOT NULL
          AND sc.latitude IS NOT NULL AND sc.longitude IS NOT NULL
    """, (state_code, year))

    schools = []
    for row in cur:
        name, lat, lon, city, district, prof_rate, nces_id = row
        schools.append({
            'name': name,
            'lat': lat,
            'lon': lon,
            'proficiency': round(prof_rate / 100, 4),  # Convert percentage to 0-1
            'district': district or '',
            'city': city or '',
            'ncessch': nces_id or ''
        })

    if not schools:
        return None

    # Build aggregates
    districts, cities = build_aggregates(schools)

    # NAEP data
    naep_state = naep_data['states'].get(state_code, naep_data['national']['US'])

    return {
        'state': state_code,
        'stateName': STATE_NAMES.get(state_code, state_code),
        'year': year,
        'naep': {
            'text': naep_state['text'],
            'numerator': naep_state.get('numerator', 0),
            'denominator': naep_state.get('denominator', 0)
        },
        'schools': schools,
        'districts': districts,
        'cities': cities
    }


def build_aggregates(schools):
    """Build district and city aggregates from school data."""

    # District aggregation
    district_data = defaultdict(lambda: {'lats': [], 'lons': [], 'profs': []})
    for s in schools:
        d = s['district']
        if d:
            district_data[d]['lats'].append(s['lat'])
            district_data[d]['lons'].append(s['lon'])
            district_data[d]['profs'].append(s['proficiency'])

    districts = []
    for name, data in district_data.items():
        n = len(data['profs'])
        districts.append({
            'name': name,
            'lat': sum(data['lats']) / n,
            'lon': sum(data['lons']) / n,
            'proficiency': round(sum(data['profs']) / n, 4),
            'schoolCount': n
        })

    # City aggregation
    city_data = defaultdict(lambda: {'lats': [], 'lons': [], 'profs': []})
    for s in schools:
        c = s['city']
        if c:
            city_data[c]['lats'].append(s['lat'])
            city_data[c]['lons'].append(s['lon'])
            city_data[c]['profs'].append(s['proficiency'])

    cities = []
    for name, data in city_data.items():
        n = len(data['profs'])
        cities.append({
            'name': name,
            'lat': sum(data['lats']) / n,
            'lon': sum(data['lons']) / n,
            'proficiency': round(sum(data['profs']) / n, 4),
            'schoolCount': n
        })

    return districts, cities


def main():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database not found at {DB_PATH}")
        print("Make sure the external drive is connected.")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    naep = load_naep()
    conn = sqlite3.connect(DB_PATH)

    success = 0
    skipped = 0

    for state_code in sorted(STATE_NAMES.keys()):
        print(f"Processing {state_code} ({STATE_NAMES[state_code]})...", end=" ")

        result = extract_state(conn, state_code, naep)

        if not result:
            print("SKIPPED (no data)")
            skipped += 1
            continue

        out_path = os.path.join(OUTPUT_DIR, f"{state_code}.json")
        with open(out_path, 'w') as f:
            json.dump(result, f)

        print(f"year={result['year']}, {len(result['schools'])} schools, "
              f"{len(result['districts'])} districts, {len(result['cities'])} cities")
        success += 1

    conn.close()

    print(f"\nDone! {success} states extracted, {skipped} skipped.")
    print(f"Intermediate files in: {OUTPUT_DIR}")
    print("Next: run prepare-map-data.js to project coordinates.")


if __name__ == '__main__':
    main()
