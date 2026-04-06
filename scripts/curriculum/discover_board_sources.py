"""Discover district board/governance source URLs and add to source_registry.

Usage:
    python scripts/curriculum/discover_board_sources.py --limit 2000
"""

import argparse
import concurrent.futures
import os
import sqlite3
import sys
from datetime import date
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(__file__))
from extract import fetch_page, is_board_platform_domain

DB_PATH = os.path.join(os.path.dirname(__file__), "curriculum.db")
TODAY = date.today().isoformat()


def detect_platform(url):
    low = (url or "").lower()
    if "boarddocs.com" in low:
        return "boarddocs"
    if "simbli.eboardsolutions.com" in low:
        return "simbli"
    if "legistar" in low:
        return "legistar"
    if "boardbook" in low:
        return "boardbook"
    if "granicus" in low:
        return "granicus"
    return "board_platform"


def load_targets(conn, limit, pending_only, min_schools):
    cur = conn.cursor()
    query = """
        SELECT s.id, s.leaid, d.district_name, d.state, s.url
        FROM source_registry s
        JOIN districts d ON d.leaid = s.leaid
        WHERE s.source_type = 'district_website'
          AND COALESCE(d.school_count, 0) >= ?
    """
    params = [min_schools]
    if pending_only:
        query += " AND COALESCE(s.crawl_status, 'pending') = 'pending'"
    query += " ORDER BY d.school_count DESC, d.state, d.district_name LIMIT ?"
    params.append(limit)
    cur.execute(query, params)
    return cur.fetchall()


def insert_board_source(cur, leaid, url):
    domain = (urlparse(url).netloc or "").lower().lstrip("www.")
    cur.execute(
        """
        INSERT OR IGNORE INTO source_registry
        (leaid, source_type, url, domain, platform_hint, crawl_status)
        VALUES (?, 'board_minutes', ?, ?, ?, 'pending')
        """,
        (leaid, url, domain, detect_platform(url)),
    )
    return cur.rowcount


def scan_target(row, timeout):
    source_id, leaid, district_name, state, url = row
    try:
        _text, _ctype, _hash, links = fetch_page(url, timeout=timeout)
    except Exception as exc:
        return {
            "ok": False,
            "source_id": source_id,
            "leaid": leaid,
            "district_name": district_name,
            "state": state,
            "url": url,
            "error": str(exc),
            "board_links": [],
        }

    board_links = []
    seen = set()
    for link in links:
        domain = (urlparse(link).netloc or "").lower().lstrip("www.")
        if not is_board_platform_domain(domain):
            continue
        if link in seen:
            continue
        seen.add(link)
        board_links.append(link)

    return {
        "ok": True,
        "source_id": source_id,
        "leaid": leaid,
        "district_name": district_name,
        "state": state,
        "url": url,
        "error": "",
        "board_links": board_links,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000, help="Max district websites to scan")
    parser.add_argument("--timeout", type=int, default=5, help="Fetch timeout seconds")
    parser.add_argument("--workers", type=int, default=24, help="Parallel workers")
    parser.add_argument("--min-schools", type=int, default=1, help="Minimum district school count")
    parser.add_argument("--pending-only", action="store_true", help="Only scan pending district_website rows")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    targets = load_targets(
        conn,
        limit=args.limit,
        pending_only=args.pending_only,
        min_schools=args.min_schools,
    )
    print(f"Scanning {len(targets)} district websites")

    scanned = 0
    with_links = 0
    inserted = 0
    errors = 0

    futures = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for row in targets:
            fut = ex.submit(scan_target, row, args.timeout)
            futures[fut] = row

        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            result = fut.result()
            scanned += 1
            if not result["ok"]:
                errors += 1
            else:
                board_links = result["board_links"]
                if board_links:
                    with_links += 1
                    add = 0
                    for link in board_links:
                        add += insert_board_source(cur, result["leaid"], link)
                    inserted += add

            if i % 100 == 0 or i == len(targets):
                conn.commit()
            if i % 50 == 0 or i == len(targets):
                print(
                    f"  progress {i}/{len(targets)} | scanned={scanned} "
                    f"with_links={with_links} inserted={inserted} errors={errors}"
                )

    cur.execute(
        """
        INSERT INTO run_logs (run_date, phase, status, districts_processed, districts_found, errors, notes)
        VALUES (?, 'board_source_discovery', 'success', ?, ?, ?, ?)
        """,
        (
            TODAY,
            scanned,
            inserted,
            errors,
            (
                f"limit={args.limit}, pending_only={args.pending_only}, "
                f"min_schools={args.min_schools}, workers={args.workers}"
            ),
        ),
    )
    conn.commit()

    print("\n=== Discovery Summary ===")
    print(f"Scanned: {scanned}")
    print(f"Districts with board links: {with_links}")
    print(f"Sources inserted: {inserted}")
    print(f"Errors: {errors}")
    conn.close()


if __name__ == "__main__":
    main()
