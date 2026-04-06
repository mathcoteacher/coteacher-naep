"""Tier 1: Rhode Island curriculum data from RIDE (RI Dept of Education)

Source: The 2024-2025 Curriculum Survey Report PDF lists K-12 ELA and Math
curricula for each LEA. We also have a Tableau dashboard as backup.

PDF: https://ride.ri.gov/sites/g/files/xkgbur806/files/2025-05/24-25%20Curriculum%20Survey%20Report.pdf

Strategy: Download the PDF, parse it to extract district-level math
curriculum for K-5 and 6-8.

Usage:
    python scripts/curriculum/tier1_rhode_island.py
"""

import os
import re
import sqlite3
import ssl
import subprocess
import sys
import urllib.request
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
from normalize import load_normalization_map, normalize_curriculum_name

DB_PATH = os.path.join(os.path.dirname(__file__), "curriculum.db")
PDF_URL = "https://ride.ri.gov/sites/g/files/xkgbur806/files/2025-05/24-25%20Curriculum%20Survey%20Report.pdf"
PDF_PATH = os.path.join(os.path.dirname(__file__), "seed_data", "ri_curriculum_survey_2024_25.pdf")
SOURCE_URL = "https://ride.ri.gov/instruction-assessment/curriculum/curriculum-used-rhode-island"
SOURCE_TIER = 1
TODAY = date.today().isoformat()


def download_pdf():
    """Download the PDF if not already cached."""
    if os.path.exists(PDF_PATH):
        print(f"Using cached PDF: {PDF_PATH}")
        return

    print(f"Downloading {PDF_URL}...")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(PDF_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, context=ctx) as resp:
        data = resp.read()
    with open(PDF_PATH, "wb") as f:
        f.write(data)
    print(f"  Saved to {PDF_PATH} ({len(data)} bytes)")


def extract_text_from_pdf():
    """Extract text from the PDF using pdftotext or Python fallback."""
    # Try pdftotext (from poppler) first — best quality
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", PDF_PATH, "-"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            print("  Extracted text using pdftotext")
            return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: try PyPDF2 or pdfplumber
    try:
        import pdfplumber
        text = ""
        with pdfplumber.open(PDF_PATH) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""
                text += "\n\n"
        print("  Extracted text using pdfplumber")
        return text
    except ImportError:
        pass

    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(PDF_PATH)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
            text += "\n\n"
        print("  Extracted text using PyPDF2")
        return text
    except ImportError:
        pass

    print("ERROR: No PDF extraction tool available.")
    print("  Install one of: poppler (brew install poppler), pdfplumber (pip install pdfplumber), or PyPDF2")
    sys.exit(1)


def parse_ri_curriculum_text(text):
    """Parse the RI curriculum survey text to extract district math data.

    The PDF has a layout where each line contains:
    - District name (left-aligned, first ~25 chars) — only on first line of each LEA block
    - Math curriculum entry ("Grades K-5: ..." around chars 25-85)
    - ELA curriculum entry (after char 85)

    District names may also span multiple lines (district name on one line, continuation on next).

    Returns dict: district_name -> {k5: curriculum_name, g68: curriculum_name}
    """
    districts = {}
    lines = text.split("\n")
    current_district = None

    # Grade entry pattern — matches "Grades K-5: Eureka Math (Great Minds) 2018"
    grade_entry = re.compile(
        r'Grades?\s*(K-?\s*\d+|[\d]-?\d+)\s*:?\s*(.+)',
        re.IGNORECASE,
    )
    # High School pattern
    hs_pattern = re.compile(r'High School[^:]*:\s*(.+)', re.IGNORECASE)
    # Math-specific pattern for lines like "Math 6-8: ..."
    math_direct = re.compile(r'Math\s*(K-?\d+|[\d]-?\d+)\s*:\s*(.+)', re.IGNORECASE)

    # Two-pass approach:
    # Pass 1: Extract all (line_number, district_name, grade_range, curriculum) entries
    # Pass 2: Assign orphan grade entries to the nearest district

    past_header = False
    entries = []  # (line_idx, name_or_none, grade_range_or_none, curriculum_or_none)

    for i, line in enumerate(lines):
        if not line.strip():
            continue

        if "LEA" in line and "Math" in line and "English" in line:
            past_header = True
            continue
        if not past_header:
            continue

        raw = line

        # Find where grade entry starts
        grade_match = re.search(r'(?:Grades?\s|High School|Math\s[K\d])', raw)
        if grade_match:
            name_part = raw[:grade_match.start()].strip()
            math_and_ela = raw[grade_match.start():]
        else:
            name_part = raw.strip()
            math_and_ela = ""

        # Determine district name (if present on this line)
        district_name = None
        if name_part and len(name_part) > 2:
            if not any(name_part.lower().startswith(x) for x in
                       ["meets ", "partially ", "does not", "locally ", "has not",
                        "approved ", "ride ", "yellow", "each local", "update",
                        "reveal math", "*latest", "not yet"]):
                district_name = name_part

        # Extract math curriculum
        grade_range = None
        curriculum = None
        if math_and_ela:
            if hs_pattern.match(math_and_ela):
                pass  # Skip high school
            else:
                m = grade_entry.match(math_and_ela) or math_direct.match(math_and_ela)
                if m:
                    grade_range = m.group(1).strip()
                    curriculum = m.group(2).strip()

                    # Truncate at ELA column
                    ela_boundary = re.search(r'\s{3,}Grades?\s', curriculum)
                    if ela_boundary:
                        curriculum = curriculum[:ela_boundary.start()].strip()
                    whitespace_gap = re.search(r'\s{5,}', curriculum)
                    if whitespace_gap:
                        curriculum = curriculum[:whitespace_gap.start()].strip()

        if district_name or (grade_range and curriculum):
            entries.append((i, district_name, grade_range, curriculum))

    # Pass 2: Associate entries with districts
    # In the PDF layout, the district name appears in the MIDDLE of a multi-line block.
    # K-5 often appears on the line ABOVE the district name.
    # 6-8 appears on the SAME line as or BELOW the district name.
    # Strategy: When we see a district name, claim the previous unclaimed K-5 entry.

    current_district = None
    pending_k5 = None  # K-5 entry waiting to be claimed by next district name

    for idx, (line_idx, name, grade_range, curriculum) in enumerate(entries):
        if name:
            # New district detected
            current_district = name

            # Claim any pending K-5 entry from previous line
            if pending_k5 and current_district:
                if current_district not in districts:
                    districts[current_district] = {"k5": None, "g68": None}
                districts[current_district]["k5"] = pending_k5
                pending_k5 = None

        if grade_range and curriculum and current_district:
            grade_lower = grade_range.lower().replace(" ", "")

            is_k5 = bool(re.match(r'k-?[2-6]', grade_lower) or grade_lower in ("3-5",))
            is_68 = bool(re.match(r'[5-8]-[5-8]', grade_lower))

            if current_district not in districts:
                districts[current_district] = {"k5": None, "g68": None}

            if is_k5:
                districts[current_district]["k5"] = curriculum
            elif is_68:
                districts[current_district]["g68"] = curriculum
        elif grade_range and curriculum and not current_district:
            # No district yet — this might be a K-5 entry before the district name
            grade_lower = grade_range.lower().replace(" ", "")
            is_k5 = bool(re.match(r'k-?[2-6]', grade_lower) or grade_lower in ("3-5",))
            if is_k5:
                pending_k5 = curriculum

        # Also handle case where K-5 is on a line with no district name
        # and appears BEFORE the next district name entry
        if grade_range and curriculum and not name:
            grade_lower = grade_range.lower().replace(" ", "")
            is_k5 = bool(re.match(r'k-?[2-6]', grade_lower) or grade_lower in ("3-5",))
            # Look ahead: is the next entry a district name?
            if is_k5 and idx + 1 < len(entries):
                next_name = entries[idx + 1][1]
                if next_name and next_name != current_district:
                    # This K-5 belongs to the upcoming district
                    pending_k5 = curriculum

    return districts


def build_ri_district_lookup(conn):
    """Build RI district name -> LEAID lookup."""
    cur = conn.cursor()
    cur.execute("SELECT leaid, district_name FROM districts WHERE state = 'RI'")
    rows = cur.fetchall()

    lookup = {}
    for leaid, name in rows:
        lookup[name.lower().strip()] = leaid
        for suffix in [" school district", " public schools", " schools"]:
            if name.lower().endswith(suffix):
                lookup[name.lower().replace(suffix, "").strip()] = leaid

    return lookup


def match_ri_district(name, lookup):
    """Match an RI district name to LEAID."""
    name_lower = name.strip().lower()

    if name_lower in lookup:
        return lookup[name_lower]

    # Substring match
    for key, leaid in lookup.items():
        if key in name_lower or name_lower in key:
            if len(key) > 3:
                return leaid

    return None


def main():
    conn = sqlite3.connect(DB_PATH)
    mapping = load_normalization_map()

    # Download PDF
    download_pdf()

    # Extract text
    print("Extracting text from PDF...")
    text = extract_text_from_pdf()
    print(f"  Extracted {len(text)} characters")

    # Save extracted text for debugging
    text_path = PDF_PATH.replace(".pdf", ".txt")
    with open(text_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"  Saved text to {text_path}")

    # Parse curriculum data
    print("\nParsing curriculum data...")
    curricula = parse_ri_curriculum_text(text)
    print(f"  Found {len(curricula)} districts with math curriculum data")

    if not curricula:
        print("\nPDF parsing didn't find structured data.")
        print("The PDF format may need manual inspection.")
        print(f"Review the extracted text at: {text_path}")
        print("You may need to adjust parse_ri_curriculum_text() for the actual format.")

        # Show first 2000 chars for debugging
        print(f"\nFirst 2000 chars of extracted text:")
        print(text[:2000])
        conn.close()
        return

    # Build lookup and match
    ri_lookup = build_ri_district_lookup(conn)
    print(f"\nOur RI districts in DB: {len(ri_lookup)}")

    matched = 0
    inserted = 0
    unmatched = []
    cur = conn.cursor()

    for district_name, data in sorted(curricula.items()):
        leaid = match_ri_district(district_name, ri_lookup)
        if not leaid:
            unmatched.append(district_name)
            continue

        matched += 1

        k5_raw = data.get("k5")
        g68_raw = data.get("g68")

        k5_norm = normalize_curriculum_name(k5_raw, mapping)[0] if k5_raw else None
        g68_norm = normalize_curriculum_name(g68_raw, mapping)[0] if g68_raw else None

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

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
