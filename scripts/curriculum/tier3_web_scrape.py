"""Tier 3: Agentic web scraping for large districts without curriculum data.

Searches DuckDuckGo for curriculum adoption info, fetches result pages,
and uses pattern matching against known curriculum names to extract data.

Targets districts with 10+ schools not already covered by Tier 1/2.

Usage:
    python scripts/curriculum/tier3_web_scrape.py [--limit N] [--offset N]
"""

import argparse
import os
import re
import sqlite3
import ssl
import sys
import time
import urllib.request
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
from normalize import load_normalization_map, normalize_curriculum_name

DB_PATH = os.path.join(os.path.dirname(__file__), "curriculum.db")
SOURCE_TIER = 3
TODAY = date.today().isoformat()

# Known curriculum names for pattern matching in web page text
KNOWN_CURRICULA_K5 = [
    "Eureka Math", "Eureka Math2", "EngageNY",
    "Illustrative Mathematics", "IM K-5",
    "enVision Mathematics", "enVision Math", "enVisionmath",
    "Into Math", "HMH Into Math",
    "Bridges in Mathematics",
    "Reveal Math",
    "Saxon Math",
    "Go Math",
    "Ready Classroom Mathematics", "iReady Classroom",
    "Zearn Math", "Zearn",
    "Everyday Mathematics",
    "Investigations in Number",
    "Math in Focus", "Singapore Math",
    "ORIGO Stepping Stones",
    "My Math", "McGraw-Hill My Math",
    "Math Expressions",
]

KNOWN_CURRICULA_68 = [
    "Illustrative Mathematics", "Open Up Resources",
    "Reveal Math",
    "enVision Mathematics", "enVision Math",
    "Big Ideas Math",
    "Into Math", "HMH Into Math",
    "Carnegie Learning", "MATHia",
    "Connected Mathematics", "CMP3",
    "Ready Classroom Mathematics", "iReady Classroom",
    "Desmos Math",
    "Eureka Math", "Eureka Math2",
    "College Preparatory Mathematics", "CPM",
    "Saxon Math",
    "Go Math",
    "SpringBoard Mathematics",
    "EdGems Math",
]


def get_target_districts(conn, limit=50, offset=0):
    """Get districts with 10+ schools not already in curriculum table."""
    cur = conn.cursor()
    cur.execute("""
        SELECT d.leaid, d.district_name, d.state, d.school_count
        FROM districts d
        LEFT JOIN curriculum c ON d.leaid = c.leaid
        WHERE c.leaid IS NULL AND d.school_count >= 10
        ORDER BY d.school_count DESC
        LIMIT ? OFFSET ?
    """, (limit, offset))
    return cur.fetchall()


def clean_district_name_for_search(district_name):
    """Convert NCES formal district name to a more searchable form.

    Examples:
      'City of Chicago SD 299' -> 'Chicago Public Schools'
      'MIAMI-DADE' -> 'Miami-Dade County Public Schools'
      'School District No. 1 in the county of Denver...' -> 'Denver Public Schools'
      'Philadelphia City SD' -> 'Philadelphia School District'
    """
    name = district_name.strip()

    # Handle all-caps names (FL counties) — add "County Public Schools"
    if name.isupper():
        name = name.title()
        if len(name.split()) <= 2:
            return f"{name} County Public Schools"
        return name

    # Strip "City of " prefix
    name = re.sub(r'^City of\s+', '', name)
    # Strip trailing " SD NNN" patterns
    name = re.sub(r'\s+SD\s*\d*$', '', name)
    # Strip "School District No. N in the county of ..." → extract county/city
    m = re.match(r'School District No\.\s*\d+\s*(?:in the county of\s+)?(.+)', name, re.IGNORECASE)
    if m:
        name = m.group(1).split(' and ')[0].strip()
    # Strip trailing "City" if the name is like "Philadelphia City"
    name = re.sub(r'\s+City$', '', name)
    # Strip "Consolidated" / "Independent" / "Unified" / "Municipal" etc.
    # but keep these for the search — they're often part of the known name
    # Just clean up the search-unfriendly parts

    # If the result is short, add context
    if len(name.split()) <= 2 and 'school' not in name.lower():
        name = f"{name} Public Schools"

    return name


def search_district_curriculum(district_name, state_name):
    """Search DuckDuckGo for district curriculum information.

    Returns list of (title, url, snippet) tuples.
    """
    from ddgs import DDGS

    search_name = clean_district_name_for_search(district_name)

    queries = [
        f'"{search_name}" math curriculum adopted',
        f'"{search_name}" math textbook adoption',
        f'"{search_name}" math instructional materials',
    ]

    results = []
    seen_urls = set()
    for query in queries:
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=5):
                    url = r.get("href", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        results.append((r.get("title", ""), url, r.get("body", "")))
        except Exception as e:
            print(f"    Search error for '{query}': {e}")
        time.sleep(1.5)  # Rate limit

    return results


def fetch_page_text(url, timeout=10):
    """Fetch a web page and extract text content."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        })
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return ""
            html = resp.read(100_000).decode("utf-8", errors="ignore")

        # Simple HTML to text conversion
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        # Remove script and style elements
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)
        return text[:20_000]  # Limit text size

    except Exception:
        return ""


STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DC": "District of Columbia",
    "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "IA": "Iowa", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def extract_curriculum_from_text(text, district_name, search_name=None):
    """Extract K-5 and 6-8 math curriculum from page text using pattern matching.

    Returns (k5_curriculum, g68_curriculum, confidence, evidence) or (None, None, 0, "")
    """
    text_lower = text.lower()

    # Check if this page is relevant to the district and math curriculum
    # Use both the NCES name and the cleaned search name for matching
    all_words = set()
    for name in [district_name, search_name or ""]:
        for w in name.lower().split():
            if len(w) > 3:
                all_words.add(w)
    if not any(w in text_lower for w in all_words):
        return None, None, 0, ""

    if "math" not in text_lower and "curriculum" not in text_lower:
        return None, None, 0, ""

    k5_found = None
    g68_found = None
    evidence = ""

    # Search for known K-5 curricula in context of elementary/K-5 mentions
    for curriculum in KNOWN_CURRICULA_K5:
        pattern = re.compile(re.escape(curriculum), re.IGNORECASE)
        matches = list(pattern.finditer(text))
        if matches:
            for match in matches:
                # Get surrounding context (200 chars)
                start = max(0, match.start() - 200)
                end = min(len(text), match.end() + 200)
                context = text[start:end].lower()

                # Check if context mentions elementary, K-5, K-8, primary, etc.
                is_k5_context = any(kw in context for kw in [
                    "elementary", "k-5", "k-4", "k-2", "k-8", "primary",
                    "grades k", "grades 1", "grades 2", "grades 3",
                ])
                is_68_context = any(kw in context for kw in [
                    "middle school", "6-8", "6th", "7th", "8th",
                    "grades 6", "grades 7",
                ])

                if is_k5_context and not k5_found:
                    k5_found = curriculum
                    evidence = text[start:end].strip()[:200]
                elif is_68_context and not g68_found:
                    g68_found = curriculum
                    evidence = text[start:end].strip()[:200]
                elif not k5_found and not is_68_context:
                    # Default to K-5 if no grade context
                    k5_found = curriculum

    # Search for known 6-8 curricula
    for curriculum in KNOWN_CURRICULA_68:
        if g68_found:
            break
        pattern = re.compile(re.escape(curriculum), re.IGNORECASE)
        matches = list(pattern.finditer(text))
        if matches:
            for match in matches:
                start = max(0, match.start() - 200)
                end = min(len(text), match.end() + 200)
                context = text[start:end].lower()

                is_68_context = any(kw in context for kw in [
                    "middle school", "6-8", "6th", "7th", "8th",
                    "grades 6", "grades 7",
                ])

                if is_68_context:
                    g68_found = curriculum
                    evidence = text[start:end].strip()[:200]
                    break

    if k5_found or g68_found:
        # Confidence based on how specific the match was
        confidence = 0.7 if (k5_found and g68_found) else 0.5
        return k5_found, g68_found, confidence, evidence

    return None, None, 0, ""


def process_district(leaid, district_name, state, school_count, mapping, conn):
    """Search for and extract curriculum data for one district."""
    state_name = STATE_NAMES.get(state, state)
    print(f"  [{state}] {district_name} ({school_count} schools)...", end=" ", flush=True)

    # Search
    search_name = clean_district_name_for_search(district_name)
    results = search_district_curriculum(district_name, state_name)
    if not results:
        print("no search results")
        return False

    # Try each result
    best_k5 = None
    best_g68 = None
    best_confidence = 0
    best_evidence = ""
    best_url = ""

    for title, url, snippet in results[:10]:
        if not url:
            continue

        # Fetch and extract
        text = fetch_page_text(url)
        if not text:
            continue

        k5, g68, confidence, evidence = extract_curriculum_from_text(text, district_name, search_name)

        if confidence > best_confidence:
            best_k5 = k5
            best_g68 = g68
            best_confidence = confidence
            best_evidence = evidence
            best_url = url

        if best_confidence >= 0.7:
            break  # Good enough

    if best_k5 or best_g68:
        k5_norm = normalize_curriculum_name(best_k5, mapping)[0] if best_k5 else None
        g68_norm = normalize_curriculum_name(best_g68, mapping)[0] if best_g68 else None

        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO curriculum
            (leaid, k5_curriculum, k5_normalized, grade68_curriculum, grade68_normalized,
             source_tier, source_url, confidence, date_collected)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            leaid, best_k5, k5_norm, best_g68, g68_norm,
            SOURCE_TIER, best_url, best_confidence, TODAY,
        ))
        conn.commit()
        print(f"K-5={best_k5 or '?'}, 6-8={best_g68 or '?'} (conf={best_confidence})")
        return True
    else:
        print("no curriculum found")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50, help="Max districts to process")
    parser.add_argument("--offset", type=int, default=0, help="Skip first N districts")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    mapping = load_normalization_map()

    targets = get_target_districts(conn, limit=args.limit, offset=args.offset)
    print(f"Processing {len(targets)} districts (offset={args.offset}, limit={args.limit})")
    print()

    found = 0
    not_found = 0

    for leaid, name, state, school_count in targets:
        try:
            if process_district(leaid, name, state, school_count, mapping, conn):
                found += 1
            else:
                not_found += 1
        except KeyboardInterrupt:
            print("\nInterrupted!")
            break
        except Exception as e:
            print(f"    Error: {e}")
            not_found += 1

        # Rate limiting — be polite
        time.sleep(2)

    # Summary
    print(f"\n=== Results ===")
    print(f"  Found: {found}")
    print(f"  Not found: {not_found}")

    # Overall stats
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM curriculum")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM curriculum WHERE source_tier = 3")
    tier3 = cur.fetchone()[0]
    print(f"  Total districts with data: {total} (Tier 3: {tier3})")

    conn.close()


if __name__ == "__main__":
    main()
