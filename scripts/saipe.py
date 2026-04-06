#!/usr/bin/env python3
"""
Download Census SAIPE (Small Area Income & Poverty Estimates) data
for all US school districts. Outputs scripts/saipe_data.json mapping
7-digit LEAID → child poverty rate (ages 5-17, as percentage).

Source: https://api.census.gov/data/timeseries/poverty/saipe/schdist
No API key needed (under 500 queries/day).
"""

import json
import os
import ssl
import urllib.request

# Workaround for macOS Python SSL certificate issue
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

YEAR = "2024"
BASE_URL = "https://api.census.gov/data/timeseries/poverty/saipe/schdist"
VARIABLE = "SAEPOVRAT5_17RV_PT"  # child poverty ratio, ages 5-17

# All 3 district types
DISTRICT_TYPES = [
    "school district (unified)",
    "school district (elementary)",
    "school district (secondary)",
]

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "saipe_data.json")


def fetch_districts(district_type):
    """Fetch poverty data for one district type across all states."""
    geo_for = f"for={urllib.request.quote(district_type)}:*"
    url = f"{BASE_URL}?get={VARIABLE},SD_NAME,LEAID&{geo_for}&in=state:*&time={YEAR}"
    print(f"  Fetching {district_type}...")

    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
        data = json.loads(resp.read().decode())

    # First row is headers, rest is data
    # Columns: [SAEPOVRAT5_17RV_PT, SD_NAME, LEAID, time, state, district_id]
    results = {}
    for row in data[1:]:
        poverty_str, name, local_id, _, state_fips, _ = row
        if poverty_str is None or poverty_str == "":
            continue
        try:
            poverty_pct = float(poverty_str)
        except (ValueError, TypeError):
            continue
        # Full LEAID = 2-digit state FIPS + 5-digit local ID
        leaid = state_fips + local_id
        results[leaid] = poverty_pct

    print(f"    Got {len(results)} districts")
    return results


def main():
    print(f"Downloading SAIPE school district poverty data (year {YEAR})...\n")

    all_districts = {}
    for dtype in DISTRICT_TYPES:
        results = fetch_districts(dtype)
        all_districts.update(results)

    print(f"\nTotal: {len(all_districts)} districts with poverty data")

    with open(OUTPUT_PATH, "w") as f:
        json.dump(all_districts, f)

    print(f"Written to {OUTPUT_PATH}")

    # Quick stats
    values = list(all_districts.values())
    values.sort()
    print(f"Poverty rate range: {values[0]:.1f}% – {values[-1]:.1f}%")
    print(f"Median: {values[len(values)//2]:.1f}%")


if __name__ == "__main__":
    main()
