"""Phase 2: Build source registry from NCES CCD district websites.

Primary path:
- Read local CCD LEA CSV (with WEBSITE field) from mounted raw data drive.

Fallback path:
- NCES Edge API (website not available) + NCES district directory links.

Usage:
    python scripts/curriculum/source_registry.py
"""

import csv
import json
import os
import re
import sqlite3
import ssl
import sys
import time
import urllib.request
from datetime import date
from urllib.parse import urlparse

DB_PATH = os.path.join(os.path.dirname(__file__), "curriculum.db")
CACHE_PATH = os.path.join(os.path.dirname(__file__), "seed_data", "nces_districts.json")
TODAY = date.today().isoformat()

CCD_LEA_CSV_CANDIDATES = [
    os.environ.get("NCES_CCD_LEA_CSV", "").strip(),
    "/Volumes/SignatureMi/ohio_education_data/data/raw/nces_ccd/ccd_lea_029_2425_w_0a_051425.csv",
]

# Known state DOE curriculum dashboards / datasets
STATE_DOE_SOURCES = {
    "MA": {
        "url": "https://curriculumdashboard.mass.gov/explore",
        "source_type": "state_doe",
        "platform_hint": "nextjs_ssg",
    },
    "NE": {
        "url": "https://nematerialsmatter.org",
        "source_type": "state_doe",
        "platform_hint": "xlsx_download",
    },
    "RI": {
        "url": "https://ride.ri.gov",
        "source_type": "state_doe",
        "platform_hint": "pdf_report",
    },
    "LA": {
        "url": "https://www.louisianabelieves.com/academics/ONLINE-INSTRUCTIONAL-MATERIALS-REVIEWS/curricular-resources-annotated-reviews",
        "source_type": "state_doe",
        "platform_hint": "state_review_list",
    },
    "NM": {
        "url": "https://webnew.ped.state.nm.us/bureaus/instructional-material/adopted-instructional-material/",
        "source_type": "state_doe",
        "platform_hint": "state_adoption_list",
    },
    "TX": {
        "url": "https://tea.texas.gov/academics/instructional-materials/review-and-adoption-process",
        "source_type": "state_doe",
        "platform_hint": "state_adoption_list",
    },
    "FL": {
        "url": "https://www.fldoe.org/academics/standards/instructional-materials/",
        "source_type": "state_doe",
        "platform_hint": "state_adoption_list",
    },
    "CA": {
        "url": "https://www.cde.ca.gov/ci/ma/im/",
        "source_type": "state_doe",
        "platform_hint": "state_adoption_list",
    },
    "NY": {
        "url": "https://www.nysed.gov/curriculum-instruction/mathematics",
        "source_type": "state_doe",
        "platform_hint": "state_doe_page",
    },
    "OH": {
        "url": "https://education.ohio.gov/Topics/Learning-in-Ohio/Mathematics",
        "source_type": "state_doe",
        "platform_hint": "state_doe_page",
    },
}


def normalize_url(url):
    """Normalize a district website URL."""
    if not url:
        return None

    url = url.strip()
    if not url or url.lower() in {"n/a", "na", "none", "null"}:
        return None

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    url = url.split("#")[0].strip()
    while url.endswith("/"):
        url = url[:-1]

    return url if url else None


def normalize_domain(url):
    """Extract and normalize domain from URL."""
    if not url:
        return None

    try:
        parsed = urlparse(url)
        domain = (parsed.netloc or "").lower()
        domain = re.sub(r"^www\.", "", domain)
        domain = domain.split(":")[0]
        return domain or None
    except Exception:
        return None


def detect_board_platform(url, domain):
    """Detect board platform hints from URL/domain."""
    combined = f"{url or ''} {domain or ''}".lower()
    if "boarddocs.com" in combined:
        return "boarddocs"
    if "simbli.eboardsolutions.com" in combined:
        return "simbli"
    if "boardbook" in combined:
        return "boardbook"
    if "legistar" in combined:
        return "legistar"
    if "boardontrack" in combined:
        return "boardontrack"
    return None


def load_ccd_lea_records():
    """Load LEA records from local CCD LEA CSV with WEBSITE field."""
    csv_path = None
    for candidate in CCD_LEA_CSV_CANDIDATES:
        if candidate and os.path.exists(candidate):
            csv_path = candidate
            break

    if not csv_path:
        return None, None

    print(f"  Loading CCD LEA CSV: {csv_path}")
    records = []
    with open(csv_path, newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            leaid = (row.get("LEAID") or "").strip()
            if not leaid:
                continue
            leaid = leaid.zfill(7)
            records.append(
                {
                    "leaid": leaid,
                    "name": (row.get("LEA_NAME") or "").strip(),
                    "state": (row.get("ST") or "").strip(),
                    "website": (row.get("WEBSITE") or "").strip(),
                    "phone": (row.get("PHONE") or "").strip(),
                    "city": (row.get("LCITY") or "").strip(),
                    "lea_type": (row.get("LEA_TYPE_TEXT") or "").strip(),
                    "charter": (row.get("CHARTER_LEA") or "").strip(),
                    "operational_schools": (row.get("OPERATIONAL_SCHOOLS") or "").strip(),
                }
            )

    print(f"  Loaded {len(records)} CCD LEA records")
    return records, csv_path


def fetch_nces_edge_records():
    """Fallback: fetch LEA records from NCES Edge API (no website field)."""
    api_base = (
        "https://nces.ed.gov/opengis/rest/services/K12_School_Locations/"
        "EDGE_GEOCODE_PUBLICLEA_2425/MapServer/0/query"
    )

    print("  Fetching NCES Edge API records (fallback)...")
    all_records = []
    offset = 0
    batch_size = 2000

    while True:
        params = (
            "?where=1%3D1"
            "&outFields=LEAID,NAME,STATE"
            "&returnGeometry=false"
            "&f=json"
            f"&resultRecordCount={batch_size}"
            f"&resultOffset={offset}"
        )
        url = api_base + params

        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if "error" in data:
                print(f"    API error: {data['error'].get('message')}")
                break

            features = data.get("features", [])
            if not features:
                break

            for feature in features:
                attrs = feature.get("attributes", {})
                leaid = str(attrs.get("LEAID", "")).strip().zfill(7)
                if not leaid:
                    continue
                all_records.append(
                    {
                        "leaid": leaid,
                        "name": attrs.get("NAME", ""),
                        "state": attrs.get("STATE", ""),
                        "website": "",
                    }
                )

            print(f"    fetched {len(all_records)}...", flush=True)
            offset += batch_size
            if len(features) < batch_size:
                break
            time.sleep(0.2)

        except Exception as exc:
            print(f"    API error at offset {offset}: {exc}")
            break

    return all_records


def load_nces_records():
    """Load records from best available source and cache to JSON."""
    records, csv_path = load_ccd_lea_records()
    if records is not None:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(records, f)
        return records, f"ccd_csv:{csv_path}"

    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            records = json.load(f)
        print(f"  Using cached records from {CACHE_PATH}: {len(records)}")
        return records, f"cache:{CACHE_PATH}"

    records = fetch_nces_edge_records()
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f)
    return records, "nces_edge_api"


def generate_nces_search_url(leaid):
    return f"https://nces.ed.gov/ccd/districtsearch/district_detail.asp?ID2={leaid}"


def clear_registry(conn):
    """Clear existing registry to avoid stale entries."""
    cur = conn.cursor()
    cur.execute("DELETE FROM source_registry")
    conn.commit()


def populate_source_registry(conn, nces_records):
    """Populate source_registry with district websites + nces + state DOE."""
    cur = conn.cursor()

    cur.execute("SELECT leaid, district_name, state FROM districts")
    districts = {r[0]: {"name": r[1], "state": r[2]} for r in cur.fetchall()}

    by_leaid = {}
    for rec in nces_records:
        leaid = str(rec.get("leaid", "")).strip().zfill(7)
        if leaid:
            by_leaid[leaid] = rec

    district_urls_added = 0
    nces_links_added = 0
    state_sources_added = 0

    for leaid, info in districts.items():
        rec = by_leaid.get(leaid)
        website = (rec or {}).get("website", "")
        url = normalize_url(website)
        if url:
            domain = normalize_domain(url)
            platform = detect_board_platform(url, domain)
            cur.execute(
                """
                INSERT OR IGNORE INTO source_registry
                (leaid, source_type, url, domain, platform_hint, crawl_status)
                VALUES (?, 'district_website', ?, ?, ?, 'pending')
                """,
                (leaid, url, domain, platform),
            )
            if cur.rowcount > 0:
                district_urls_added += 1

        nces_url = generate_nces_search_url(leaid)
        cur.execute(
            """
            INSERT OR IGNORE INTO source_registry
            (leaid, source_type, url, domain, platform_hint, crawl_status)
            VALUES (?, 'nces_directory', ?, 'nces.ed.gov', NULL, 'pending')
            """,
            (leaid, nces_url),
        )
        if cur.rowcount > 0:
            nces_links_added += 1

        if info["state"] in STATE_DOE_SOURCES:
            src = STATE_DOE_SOURCES[info["state"]]
            cur.execute(
                """
                INSERT OR IGNORE INTO source_registry
                (leaid, source_type, url, domain, platform_hint, crawl_status)
                VALUES (?, ?, ?, ?, ?, 'pending')
                """,
                (
                    leaid,
                    src["source_type"],
                    src["url"],
                    normalize_domain(src["url"]),
                    src.get("platform_hint"),
                ),
            )
            if cur.rowcount > 0:
                state_sources_added += 1

    conn.commit()
    return district_urls_added, nces_links_added, state_sources_added


def print_registry_stats(conn):
    cur = conn.cursor()
    print("\n=== Source Registry Stats ===")

    cur.execute("SELECT COUNT(*) FROM source_registry")
    print(f"Total entries: {cur.fetchone()[0]}")

    cur.execute(
        "SELECT source_type, COUNT(*) FROM source_registry GROUP BY source_type ORDER BY COUNT(*) DESC"
    )
    for stype, cnt in cur.fetchall():
        print(f"  {stype}: {cnt}")

    cur.execute("SELECT COUNT(DISTINCT leaid) FROM source_registry")
    covered = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM districts")
    total = cur.fetchone()[0]
    print(f"Districts with at least one source: {covered}/{total} ({covered/total*100:.1f}%)")

    cur.execute("SELECT COUNT(DISTINCT leaid) FROM source_registry WHERE source_type='district_website'")
    with_site = cur.fetchone()[0]
    print(f"Districts with district website: {with_site}/{total} ({with_site/total*100:.1f}%)")


def main():
    print(f"Database: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='source_registry'")
    if not cur.fetchone():
        print("ERROR: source_registry table not found. Run migrate.py first.")
        sys.exit(1)

    print("\n1. Loading NCES/CCD records...")
    nces_records, source_note = load_nces_records()
    print(f"   Records: {len(nces_records)}")
    with_website = sum(1 for r in nces_records if normalize_url(r.get('website', '')))
    print(f"   Records with website: {with_website}")

    print("\n2. Rebuilding source registry...")
    clear_registry(conn)
    district_urls_added, nces_links_added, state_sources_added = populate_source_registry(conn, nces_records)
    print(f"   district_website inserted: {district_urls_added}")
    print(f"   nces_directory inserted: {nces_links_added}")
    print(f"   state_doe inserted: {state_sources_added}")

    cur.execute(
        """
        INSERT INTO run_logs (run_date, phase, status, notes)
        VALUES (?, 'source_registry', 'success', ?)
        """,
        (
            TODAY,
            f"records={len(nces_records)}, source={source_note}, websites={with_website}",
        ),
    )
    conn.commit()

    print_registry_stats(conn)
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
