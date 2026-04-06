"""Phase 3: Quality-first extraction engine.

Primary behavior:
- Crawl official sources from source_registry (district websites first).
- Require district identity + grade-band context + curriculum mention.
- Store fetched documents and evidence snippets for auditability.

Optional behavior:
- DuckDuckGo fallback can be enabled with --allow-web-search.

Usage:
    python scripts/curriculum/extract.py --source-types district_website --limit 500
"""

import argparse
import concurrent.futures
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import ssl
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urljoin, urlparse

sys.path.insert(0, os.path.dirname(__file__))
from normalize import load_normalization_map, normalize_curriculum_name

try:
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except Exception:
    sync_playwright = None
    PLAYWRIGHT_AVAILABLE = False

DB_PATH = os.path.join(os.path.dirname(__file__), "curriculum.db")
SEED_FILE = os.path.join(os.path.dirname(__file__), "seed_data", "curriculum_names.csv")
TODAY = date.today().isoformat()

KNOWN_CURRICULA = [
    "Eureka Math",
    "Eureka Math2",
    "Eureka Math Squared",
    "EngageNY",
    "Illustrative Mathematics",
    "IM K-5",
    "IM 6-8",
    "enVision Mathematics",
    "enVision Math",
    "Into Math",
    "HMH Into Math",
    "Bridges in Mathematics",
    "Reveal Math",
    "Saxon Math",
    "Go Math",
    "HMH Go Math",
    "Ready Classroom Mathematics",
    "iReady Classroom",
    "i-Ready Classroom Mathematics",
    "Zearn Math",
    "Everyday Mathematics",
    "Math in Focus",
    "Singapore Math",
    "ORIGO Stepping Stones",
    "My Math",
    "Math Expressions",
    "Big Ideas Math",
    "Carnegie Learning",
    "MATHia",
    "Connected Mathematics",
    "CMP3",
    "SpringBoard Mathematics",
    "College Preparatory Mathematics",
    "CPM",
    "Desmos Math",
    "EdGems Math",
    "Core Connections",
    "STEMscopes Math",
    "Math Nation",
    "Open Up Resources",
]

AMBIGUOUS_CURRICULUM_TERMS = {
    "im",
    "great minds",
}

K5_KEYWORDS = [
    "elementary",
    "elementary school",
    "k-5",
    "k5",
    "k-4",
    "k-2",
    "k-8",
    "primary",
    "lower school",
    "grades k",
    "grades k-5",
    "grades 1",
    "grades 2",
    "grades 3",
    "grades 4",
    "grades 5",
    "kindergarten",
]

G68_KEYWORDS = [
    "middle school",
    "middle schools",
    "6-8",
    "6 to 8",
    "grades 6-8",
    "grades 6 through 8",
    "6-12",
    "grades 6-12",
    "6th",
    "7th",
    "8th",
    "grades 6",
    "grades 7",
    "grades 8",
    "junior high",
    "middle grades",
    "secondary",
]

MATH_SIGNAL_KEYWORDS = [
    "math",
    "mathematics",
    "algebra",
    "geometry",
    "number sense",
    "numeracy",
]

ADOPTION_KEYWORDS = [
    "adopt",
    "adoption",
    "curriculum",
    "instructional material",
    "textbook",
    "selected",
    "approved",
    "implementation",
    "uses",
    "using",
]

DISTRICT_STOPWORDS = {
    "school",
    "schools",
    "district",
    "county",
    "city",
    "public",
    "independent",
    "unified",
    "board",
    "education",
    "the",
    "of",
    "and",
    "for",
    "isd",
    "usd",
    "csd",
    "sd",
}

DISTRICT_ACRONYM_STOPWORDS = {
    "school",
    "schools",
    "district",
    "county",
    "city",
    "board",
    "education",
    "the",
    "of",
    "and",
    "for",
}

GRADE_RANGE_RE = re.compile(
    r"(?:grade(?:s)?\s*)?"
    r"(k|kg|kindergarten|[0-9]{1,2})(?:st|nd|rd|th)?\s*"
    r"(?:-|–|to|through)\s*"
    r"(k|kg|kindergarten|[0-9]{1,2})(?:st|nd|rd|th)?",
    re.IGNORECASE,
)
SINGLE_GRADE_RE = re.compile(r"\b([0-9]{1,2})(?:st|nd|rd|th)?\s+grade\b", re.IGNORECASE)


def load_curriculum_terms(seed_file=SEED_FILE):
    terms = set(KNOWN_CURRICULA)
    try:
        with open(seed_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                raw = (row.get("raw_name") or "").strip()
                if not raw:
                    continue
                terms.add(raw)
    except Exception:
        pass

    out = []
    for term in sorted(terms, key=lambda x: (-len(x), x.lower())):
        t = term.strip()
        if not t:
            continue
        t_low = t.lower()
        if t_low in AMBIGUOUS_CURRICULUM_TERMS:
            continue
        if len(t_low) <= 2:
            continue
        out.append(t)
    return out


CURRICULUM_TERMS = load_curriculum_terms()
CURRICULUM_PATTERNS = [
    (term, re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)) for term in CURRICULUM_TERMS
]

LINK_HINTS = [
    "curriculum",
    "instruction",
    "academic",
    "teaching",
    "learning",
    "mathematics",
    "math",
    "adoption",
    "textbook",
    "board",
    "agenda",
    "minutes",
    "policy",
    "meeting",
    "governance",
    "trustee",
    "boarddocs",
    "legistar",
    "simbli",
    "granicus",
]

BOARD_PLATFORM_DOMAINS = [
    "boarddocs.com",
    "simbli.eboardsolutions.com",
    "boardbook.org",
    "go.boarddocs.com",
    "legistar.com",
    "granicus.com",
    "agendaonline.net",
]


def is_board_platform_domain(domain):
    d = (domain or "").lower().lstrip("www.")
    if not d:
        return False
    return any(d.endswith(x) or x in d for x in BOARD_PLATFORM_DOMAINS)


BOARDDOCS_TERM_BANDS = {
    "Eureka Math": ("k5", "68"),
    "Illustrative Mathematics": ("k5", "68"),
    "enVision Mathematics": ("k5", "68"),
    "Ready Classroom Mathematics": ("k5", "68"),
    "Bridges in Mathematics": ("k5",),
    "Math Expressions": ("k5",),
    "Carnegie Learning": ("68",),
    "Big Ideas Math": ("68",),
    "Desmos Math": ("68",),
    "Connected Mathematics": ("68",),
    "Open Up Resources": ("68",),
    "SpringBoard Mathematics": ("68",),
}


def extract_links(base_url, soup):
    out = []
    base_domain = (urlparse(base_url).netloc or "").lower().lstrip("www.")
    seen = set()
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        anchor_text = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).lower()
        abs_url = urljoin(base_url, href)
        parsed = urlparse(abs_url)
        if parsed.scheme not in {"http", "https"}:
            continue
        domain = (parsed.netloc or "").lower().lstrip("www.")
        low = abs_url.lower()
        combined = f"{low} {anchor_text}"

        if base_domain and domain != base_domain:
            is_board_domain = any(domain.endswith(d) or d in domain for d in BOARD_PLATFORM_DOMAINS)
            if not is_board_domain:
                continue
            if not any(k in combined for k in ["board", "agenda", "minutes", "meeting", "trustee"]):
                continue

        if not any(h in low for h in LINK_HINTS) and not any(h in anchor_text for h in LINK_HINTS):
            continue
        if abs_url in seen:
            continue
        seen.add(abs_url)
        out.append(abs_url)
    return out[:40]


def fetch_raw(url, timeout=12, max_bytes=2_500_000):
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Encoding": "identity",
            },
        )
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            raw = resp.read(max_bytes)
            return raw, content_type
    except Exception:
        return b"", ""


def post_raw(url, data_dict, timeout=12, max_bytes=2_500_000, referer=""):
    try:
        body = urlencode(data_dict or {}).encode("utf-8")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Encoding": "identity",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": referer or url,
            },
        )
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            raw = resp.read(max_bytes)
            return raw, content_type
    except Exception:
        return b"", ""


def parse_sitemap_locs(xml_bytes):
    locs = []
    try:
        root = ET.fromstring(xml_bytes)
        for node in root.iter():
            if node.tag.lower().endswith("loc") and (node.text or "").strip():
                locs.append(node.text.strip())
    except Exception:
        return []
    return locs


def get_sitemap_links(base_url, timeout=8):
    if not base_url:
        return []

    candidates = [
        urljoin(base_url + "/", "sitemap.xml"),
        urljoin(base_url + "/", "sitemap_index.xml"),
    ]
    out = []
    seen = set()
    base_domain = (urlparse(base_url).netloc or "").lower().lstrip("www.")

    def add_if_relevant(u):
        if not u:
            return
        pu = urlparse(u)
        if pu.scheme not in {"http", "https"}:
            return
        dom = (pu.netloc or "").lower().lstrip("www.")
        if base_domain and dom != base_domain:
            return
        low = u.lower()
        if not any(h in low for h in LINK_HINTS):
            return
        if u not in seen:
            seen.add(u)
            out.append(u)

    for s_url in candidates:
        raw, ctype = fetch_raw(s_url, timeout=timeout, max_bytes=2_000_000)
        if not raw:
            continue
        if "xml" not in ctype and not raw.lstrip().startswith(b"<"):
            continue
        locs = parse_sitemap_locs(raw)
        if not locs:
            continue

        sitemap_children = [u for u in locs if "sitemap" in u.lower()][:4]
        for loc in locs:
            add_if_relevant(loc)

        for child in sitemap_children:
            c_raw, c_ctype = fetch_raw(child, timeout=timeout, max_bytes=2_000_000)
            if not c_raw:
                continue
            if "xml" not in c_ctype and not c_raw.lstrip().startswith(b"<"):
                continue
            for loc in parse_sitemap_locs(c_raw):
                add_if_relevant(loc)

    return out[:60]


def boarddocs_app_path(base_url):
    parsed = urlparse(base_url or "")
    if not parsed.scheme or not parsed.netloc:
        return None, None
    path = parsed.path or ""
    idx = path.lower().find("board.nsf")
    if idx == -1:
        return None, None
    app_path = path[: idx + len("board.nsf")].strip("/")
    if not app_path:
        return None, None
    root = f"{parsed.scheme}://{parsed.netloc}"
    return root, app_path


def boarddocs_get_meeting_ids(base_url, timeout=10, max_meetings=250):
    root, app_path = boarddocs_app_path(base_url)
    if not root:
        return []
    seo_url = f"{root}/{app_path}/BD-GETMeetingsListForSEO?open&0.1"
    raw, ctype = fetch_raw(seo_url, timeout=timeout, max_bytes=4_000_000)
    if not raw:
        return []
    if "json" not in ctype and not raw.lstrip().startswith(b"["):
        return []
    try:
        data = json.loads(raw.decode("utf-8", errors="ignore"))
    except Exception:
        return []
    ids = []
    for row in data:
        if not isinstance(row, dict):
            continue
        mid = (row.get("Unique") or row.get("unique") or "").strip()
        if mid and mid not in ids:
            ids.append(mid)
        if len(ids) >= max_meetings:
            break
    return ids


def boarddocs_search_hits(base_url, term, meeting_ids, timeout=10):
    root, app_path = boarddocs_app_path(base_url)
    if not root or not meeting_ids:
        return 0
    ids_blob = "~".join(meeting_ids) + "~"
    url = f"{root}/{app_path}/BD-SearchInContext?open&0.1"
    raw, ctype = post_raw(
        url,
        data_dict={"ids": ids_blob, "searchstring": term},
        timeout=timeout,
        max_bytes=1_500_000,
        referer=f"{root}/{app_path}/vpublic?open",
    )
    if not raw:
        return 0
    txt = raw.decode("utf-8", errors="ignore")
    if "<unid>" not in txt.lower():
        return 0
    return txt.lower().count("<unid>")


def boarddocs_search_unids(base_url, term, meeting_ids, timeout=10, max_unids=20):
    """Return matching BoardDocs UNIDs for a curriculum search term."""
    root, app_path = boarddocs_app_path(base_url)
    if not root or not meeting_ids:
        return []
    ids_blob = "~".join(meeting_ids) + "~"
    url = f"{root}/{app_path}/BD-SearchInContext?open&0.1"
    raw, _ctype = post_raw(
        url,
        data_dict={"ids": ids_blob, "searchstring": term},
        timeout=timeout,
        max_bytes=2_500_000,
        referer=f"{root}/{app_path}/vpublic?open",
    )
    if not raw:
        return []
    txt = raw.decode("utf-8", errors="ignore")
    out = []
    seen = set()
    for m in re.finditer(r"<unid>([^<]+)</unid>", txt, flags=re.IGNORECASE):
        unid = (m.group(1) or "").strip()
        if not unid:
            continue
        if unid in seen:
            continue
        seen.add(unid)
        out.append(unid)
        if len(out) >= max_unids:
            break
    return out


def boarddocs_fetch_minutes_text(base_url, unid, timeout=10):
    """Fetch BoardDocs minutes HTML by UNID and return extracted plain text."""
    root, app_path = boarddocs_app_path(base_url)
    if not root or not unid:
        return "", "", ""
    url = f"{root}/{app_path}/BD-GetMinutes?open&0.1"
    raw, _ctype = post_raw(
        url,
        data_dict={"id": unid},
        timeout=timeout,
        max_bytes=3_500_000,
        referer=f"{root}/{app_path}/vpublic?open",
    )
    if not raw:
        return "", "", ""

    html = raw.decode("utf-8", errors="ignore")
    if not html.strip() or "No Access" in html:
        return "", "", ""

    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
    except Exception:
        text = re.sub(r"\s+", " ", html)

    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 120:
        return "", "", ""
    content_hash = hashlib.sha256(raw).hexdigest()[:16]
    return text[:100_000], "text/html;boarddocs_minutes", content_hash


def extract_boarddocs_document_evidence(base_url, district_name, timeout=10):
    """Fetch BoardDocs documents and return auditable evidence snippets.

    Returns rows shaped as:
      {
        "url": "...#minutes=<unid>",
        "content_type": "...",
        "content_hash": "...",
        "text": "...",
        "evidence": [ ... extract_evidence rows ... ],
      }
    """
    meeting_ids = boarddocs_get_meeting_ids(base_url, timeout=timeout, max_meetings=60)
    if not meeting_ids:
        return []

    term_order = [
        "Eureka Math",
        "Illustrative Mathematics",
        "enVision Mathematics",
        "Ready Classroom Mathematics",
        "Bridges in Mathematics",
    ]
    term_timeout = max(4, min(timeout, 6))
    all_unids = []
    seen = set()
    for term in term_order:
        unids = boarddocs_search_unids(
            base_url,
            term,
            meeting_ids=meeting_ids,
            timeout=term_timeout,
            max_unids=3,
        )
        for unid in unids:
            if unid in seen:
                continue
            seen.add(unid)
            all_unids.append(unid)
            if len(all_unids) >= 6:
                break
        if len(all_unids) >= 6:
            break

    out = []
    for unid in all_unids:
        text, content_type, content_hash = boarddocs_fetch_minutes_text(
            base_url, unid, timeout=term_timeout
        )
        if not text:
            continue
        doc_url = f"{base_url}#minutes={quote_plus(unid)}"
        ev_items = extract_evidence(
            text,
            district_name,
            source_url=doc_url,
            trusted_domain=True,
            require_adoption_signal=True,
        )
        if not ev_items:
            continue
        out.append(
            {
                "url": doc_url,
                "content_type": content_type,
                "content_hash": content_hash,
                "text": text,
                "evidence": ev_items,
            }
        )
    return out


def fetch_page(url, timeout=12):
    """Fetch a page and return (text, content_type, content_hash, links)."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Encoding": "identity",
            },
        )
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            raw = resp.read(2_500_000)
            content_hash = hashlib.sha256(raw).hexdigest()[:16]

        if "application/pdf" in content_type or url.lower().endswith(".pdf"):
            text = extract_pdf_text(raw)
            return text[:40_000], "application/pdf", content_hash, []

        if "text/html" not in content_type and "text/plain" not in content_type:
            return "", content_type, content_hash, []

        html = raw.decode("utf-8", errors="ignore")

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        links = extract_links(url, soup)
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        return text[:40_000], content_type, content_hash, links
    except Exception:
        return "", "", "", []


def extract_pdf_text(raw_bytes, max_pages=8):
    try:
        from PyPDF2 import PdfReader

        reader = PdfReader(io.BytesIO(raw_bytes))
        out = []
        for page in reader.pages[:max_pages]:
            txt = page.extract_text() or ""
            if txt:
                out.append(txt)
        return re.sub(r"\s+", " ", " ".join(out)).strip()
    except Exception:
        return ""


def fetch_page_with_playwright(url, timeout=12):
    if not PLAYWRIGHT_AVAILABLE:
        return "", "", "", []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            page.wait_for_timeout(1000)
            html = page.content()
            browser.close()

        raw = html.encode("utf-8", errors="ignore")
        content_hash = hashlib.sha256(raw).hexdigest()[:16]
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        links = extract_links(url, soup)
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        return text[:40_000], "text/html+rendered", content_hash, links
    except Exception:
        return "", "", "", []


def district_tokens(name):
    tokens = re.findall(r"[A-Za-z0-9]+", (name or "").lower())
    out = []
    for tok in tokens:
        if len(tok) < 4:
            continue
        if tok in DISTRICT_STOPWORDS:
            continue
        out.append(tok)
    return out


def district_acronyms(name):
    words = re.findall(r"[A-Za-z]+", (name or "").lower())
    core = [w for w in words if w not in DISTRICT_ACRONYM_STOPWORDS]
    out = set()
    if len(core) >= 2:
        ac = "".join(w[0] for w in core if w)
        if len(ac) >= 2:
            out.add(ac)
    if len(words) >= 2:
        ac_full = "".join(w[0] for w in words if w)
        if len(ac_full) >= 3:
            out.add(ac_full)
    return {a.lower() for a in out}


def district_identity_present(text_lower, district_name, trusted_domain=False):
    """Require district-specific lexical evidence to reduce false positives."""
    toks = district_tokens(district_name)
    acronyms = district_acronyms(district_name)
    acronym_hit = any(re.search(rf"\b{re.escape(ac)}\b", text_lower) for ac in acronyms)
    if not toks:
        return trusted_domain and acronym_hit

    matches = sum(1 for t in set(toks) if t in text_lower)
    if acronym_hit:
        matches += 1
    if trusted_domain:
        required = 1
    else:
        required = 2 if len(set(toks)) >= 2 else 1
    return matches >= required


def grade_token_to_int(token):
    if not token:
        return None
    t = token.strip().lower()
    if t in {"k", "kg", "kindergarten"}:
        return 0
    if not t.isdigit():
        return None
    try:
        return int(t)
    except Exception:
        return None


def detect_grade_bands(context_lower, source_url=""):
    is_k5 = any(kw in context_lower for kw in K5_KEYWORDS)
    is_68 = any(kw in context_lower for kw in G68_KEYWORDS)

    for match in GRADE_RANGE_RE.finditer(context_lower):
        start_grade = grade_token_to_int(match.group(1))
        end_grade = grade_token_to_int(match.group(2))
        if start_grade is None or end_grade is None:
            continue
        lo = min(start_grade, end_grade)
        hi = max(start_grade, end_grade)
        if lo <= 5 and hi >= 0:
            is_k5 = True
        if lo <= 8 and hi >= 6:
            is_68 = True

    if not is_k5 or not is_68:
        grades = [grade_token_to_int(m.group(1)) for m in SINGLE_GRADE_RE.finditer(context_lower)]
        grades = [g for g in grades if g is not None]
        if any(g <= 5 for g in grades):
            is_k5 = True
        if any(6 <= g <= 8 for g in grades):
            is_68 = True

    url_low = (source_url or "").lower()
    if any(t in url_low for t in ["elementary", "k-5", "k5", "primary"]):
        is_k5 = True
    if any(t in url_low for t in ["middle", "6-8", "6to8", "junior-high", "juniorhigh"]):
        is_68 = True
    if "6-12" in url_low:
        is_68 = True

    return is_k5, is_68


def extract_evidence(
    text,
    district_name,
    source_url="",
    trusted_domain=False,
    require_adoption_signal=False,
):
    """Extract auditable evidence with strict context rules."""
    if not text or len(text) < 120:
        return []

    text_lower = text.lower()
    has_identity = district_identity_present(
        text_lower, district_name, trusted_domain=trusted_domain
    )
    if not has_identity and not trusted_domain:
        return []

    if "math" not in text_lower and "curriculum" not in text_lower:
        return []

    page_is_k5, page_is_68 = detect_grade_bands(text_lower, source_url=source_url)
    page_has_math_signal = any(kw in text_lower for kw in MATH_SIGNAL_KEYWORDS)
    page_has_adoption_signal = any(kw in text_lower for kw in ADOPTION_KEYWORDS)
    source_url_low = (source_url or "").lower()

    evidence = []
    seen = set()

    for curriculum_name, pattern in CURRICULUM_PATTERNS:
        for match in pattern.finditer(text):
            start = max(0, match.start() - 500)
            end = min(len(text), match.end() + 500)
            context = text[start:end]
            ctx_lower = context.lower()

            has_math_signal = any(kw in ctx_lower for kw in MATH_SIGNAL_KEYWORDS)
            if not has_math_signal and not page_has_math_signal:
                continue

            is_k5, is_68 = detect_grade_bands(ctx_lower, source_url=source_url)

            has_adoption_signal = any(kw in ctx_lower for kw in ADOPTION_KEYWORDS)

            # Page-level fallback for structured curriculum pages where grade context
            # is present elsewhere on the same page (e.g., table/list layouts).
            if not is_k5 and not is_68:
                can_use_page_level = page_has_adoption_signal and any(
                    k in source_url_low for k in ("curriculum", "instruction", "math", "mathematics")
                )
                if can_use_page_level:
                    is_k5, is_68 = page_is_k5, page_is_68
                    has_adoption_signal = has_adoption_signal or page_has_adoption_signal

            # strict: require grade-band context
            if not is_k5 and not is_68:
                continue

            if require_adoption_signal and not has_adoption_signal:
                continue
            base_conf = 0.7 if has_adoption_signal else 0.6

            snippet = context.strip().replace("\n", " ")
            snippet = re.sub(r"\s+", " ", snippet)[:500]

            if is_k5:
                key = ("k5", curriculum_name, snippet[:120])
                if key not in seen:
                    seen.add(key)
                    evidence.append(
                        {
                            "grade_band": "k5",
                            "curriculum_raw": curriculum_name,
                            "snippet": snippet,
                            "confidence": base_conf,
                        }
                    )

            if is_68:
                key = ("68", curriculum_name, snippet[:120])
                if key not in seen:
                    seen.add(key)
                    evidence.append(
                        {
                            "grade_band": "68",
                            "curriculum_raw": curriculum_name,
                            "snippet": snippet,
                            "confidence": base_conf,
                        }
                    )

    return evidence


def upsert_document(cur, source_id, url, content_type, content_hash, text, status):
    """Insert document row and return id."""
    cur.execute(
        """
        SELECT id FROM documents
        WHERE source_id = ? AND url = ? AND content_hash = ?
        ORDER BY id DESC LIMIT 1
        """,
        (source_id, url, content_hash),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    snippet = re.sub(r"\s+", " ", (text or "").strip())[:500]
    cur.execute(
        """
        INSERT INTO documents (source_id, url, fetch_date, content_type, content_hash, snippet, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (source_id, url, TODAY, content_type, content_hash, snippet, status),
    )
    return cur.lastrowid


def candidate_urls(base_url):
    """Generate likely curriculum paths for a district site."""
    if not base_url:
        return []

    parsed_base = urlparse(base_url)
    base_domain = (parsed_base.netloc or "").lower().lstrip("www.")
    path = parsed_base.path or ""

    # BoardDocs has a public SEO endpoint with meeting titles/descriptions.
    if is_board_platform_domain(base_domain) and "board.nsf" in path.lower():
        root = f"{parsed_base.scheme}://{parsed_base.netloc}"
        idx = path.lower().find("board.nsf")
        app_path = path[: idx + len("board.nsf")].strip("/")
        if app_path:
            seo_url = f"{root}/{app_path}/BD-GETMeetingsListForSEO?open"
            return [seo_url, base_url]

    roots = [base_url]
    if base_url.startswith("http://"):
        roots.insert(0, "https://" + base_url[len("http://") :])

    paths = [
        "",
        "/curriculum",
        "/curriculum-instruction",
        "/curriculum_and_instruction",
        "/curriculum-and-instruction",
        "/academics",
        "/academics/curriculum",
        "/academics/mathematics",
        "/academics/math",
        "/departments/curriculum",
        "/departments/mathematics",
        "/departments/math",
        "/departments/teaching-and-learning",
        "/teaching-learning",
        "/teaching-and-learning",
        "/instruction",
        "/instruction/curriculum",
        "/instruction/mathematics",
        "/mathematics",
        "/math",
        "/page/math",
        "/our-schools/academics/math",
    ]
    out = []
    for root in roots:
        for p in paths:
            u = urljoin(root + "/", p.lstrip("/")) if p else root
            if u not in out:
                out.append(u)
    return out


def insert_candidate(cur, leaid, source_type, source_url, document_id, ev, mapping):
    norm, _ = normalize_curriculum_name(ev["curriculum_raw"], mapping)

    cur.execute(
        """
        SELECT 1 FROM extraction_candidates
        WHERE leaid = ? AND grade_band = ? AND curriculum_normalized = ? AND source_url = ?
        LIMIT 1
        """,
        (leaid, ev["grade_band"], norm, source_url),
    )
    if cur.fetchone():
        return False

    cur.execute(
        """
        INSERT INTO extraction_candidates
        (leaid, grade_band, curriculum_raw, curriculum_normalized,
         source_type, source_url, document_id, snippet,
         confidence, extraction_method, date_collected)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'context_rule', ?)
        """,
        (
            leaid,
            ev["grade_band"],
            ev["curriculum_raw"],
            norm,
            source_type,
            source_url,
            document_id,
            ev["snippet"],
            ev["confidence"],
            TODAY,
        ),
    )
    return True


def mark_source(cur, source_id, status):
    cur.execute(
        """
        UPDATE source_registry
        SET last_crawled = ?, crawl_status = ?
        WHERE id = ?
        """,
        (TODAY, status, source_id),
    )


def search_site_domain(domain, query, max_results=5, timeout=8):
    if not domain:
        return []
    normalized = domain.strip().lower()
    if "://" in normalized:
        normalized = urlparse(normalized).netloc.lower()
    normalized = normalized.split("/")[0].lstrip("www.")
    if not normalized:
        return []
    return search_ddg(f"site:{normalized} {query}", max_results=max_results, timeout=timeout)


def process_source(
    conn,
    source_row,
    mapping,
    timeout,
    max_pages,
    site_search,
    browser_fallback,
    skip_source_crawl=False,
):
    """Crawl one source row and insert evidence."""
    source_id, leaid, district_name, state, source_type, url, domain = source_row
    cur = conn.cursor()
    trusted_domain = source_type in {
        "district_website",
        "state_doe",
        "nces_directory",
        "board_minutes",
    }

    found = 0
    fetched_any = False

    if source_type == "board_minutes" and is_board_platform_domain(domain):
        try:
            docs = extract_boarddocs_document_evidence(
                url, district_name=district_name, timeout=max(6, timeout)
            )
        except Exception:
            docs = []
        if docs:
            fetched_any = True
            for doc in docs:
                doc_id = upsert_document(
                    cur,
                    source_id=source_id,
                    url=doc["url"],
                    content_type=doc["content_type"],
                    content_hash=doc["content_hash"],
                    text=doc["text"],
                    status="fetched",
                )
                for ev in doc["evidence"]:
                    if insert_candidate(cur, leaid, source_type, doc["url"], doc_id, ev, mapping):
                        found += 1
    if not skip_source_crawl:
        seed_urls = candidate_urls(url)
        if source_type == "district_website":
            sitemap_urls = get_sitemap_links(url, timeout=max(4, min(timeout, 10)))
            sitemap_set = set(sitemap_urls)
            seed_urls = sitemap_urls + [u for u in seed_urls if u not in sitemap_set]
        if max_pages > 0:
            seed_urls = seed_urls[:max_pages]
        queue = list(seed_urls)
        queued = set(queue)
        visited = set()

        while queue and (max_pages <= 0 or len(visited) < max_pages):
            candidate = queue.pop(0)
            if candidate in visited:
                continue
            visited.add(candidate)

            text, content_type, content_hash, links = fetch_page(candidate, timeout=timeout)
            if not text and browser_fallback:
                text, content_type, content_hash, links = fetch_page_with_playwright(
                    candidate, timeout=timeout
                )
            if not text:
                continue
            fetched_any = True

            for link in links:
                if link not in queued and link not in visited:
                    queue.append(link)
                    queued.add(link)

            doc_id = upsert_document(
                cur,
                source_id=source_id,
                url=candidate,
                content_type=content_type,
                content_hash=content_hash,
                text=text,
                status="fetched",
            )

            ev_items = extract_evidence(
                text,
                district_name,
                source_url=candidate,
                trusted_domain=trusted_domain,
            )
            for ev in ev_items:
                if insert_candidate(cur, leaid, source_type, candidate, doc_id, ev, mapping):
                    found += 1

    if found == 0 and site_search and source_type == "district_website" and domain:
        queries = [
            f'"{district_name}" math curriculum',
            f'"{district_name}" middle school math curriculum',
        ]
        for q in queries:
            for _, u, _ in search_site_domain(domain, q, max_results=4, timeout=max(4, timeout)):
                text, content_type, content_hash, _links = fetch_page(u, timeout=timeout)
                if not text and browser_fallback:
                    text, content_type, content_hash, _links = fetch_page_with_playwright(
                        u, timeout=timeout
                    )
                if not text:
                    continue
                fetched_any = True
                doc_id = upsert_document(
                    cur,
                    source_id=source_id,
                    url=u,
                    content_type=content_type,
                    content_hash=content_hash,
                    text=text,
                    status="fetched",
                )
                for ev in extract_evidence(
                    text,
                    district_name,
                    source_url=u,
                    trusted_domain=True,
                ):
                    if insert_candidate(cur, leaid, source_type, u, doc_id, ev, mapping):
                        found += 1
                if found >= 4:
                    break
            if found >= 4:
                break

    if found > 0:
        mark_source(cur, source_id, "success")
    elif fetched_any:
        mark_source(cur, source_id, "no_evidence")
    else:
        mark_source(cur, source_id, "fetch_failed")

    conn.commit()
    return found


def unwrap_ddg_link(href):
    if not href:
        return ""
    parsed = urlparse(href)
    if "duckduckgo.com" not in (parsed.netloc or ""):
        return href
    qs = parse_qs(parsed.query)
    uddg = qs.get("uddg", [])
    if uddg:
        return unquote(uddg[0])
    return href


def search_bing_rss(query, max_results=5, timeout=8):
    search_url = f"https://www.bing.com/search?q={quote_plus(query)}&format=rss"
    raw, ctype = fetch_raw(search_url, timeout=timeout, max_bytes=1_200_000)
    if not raw:
        return []
    if "xml" not in ctype and not raw.lstrip().startswith(b"<"):
        return []

    try:
        root = ET.fromstring(raw)
        out = []
        seen = set()
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            if not link:
                continue
            u = link
            if not u or u in seen:
                continue
            seen.add(u)
            out.append((title, u, desc))
            if len(out) >= max_results:
                break
        return out
    except Exception:
        return []


def search_ddg(query, max_results=5, timeout=8):
    """Search wrapper: DDGS first (with hard timeout), then Bing RSS fallback."""
    try:
        from ddgs import DDGS

        def _run():
            out = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    u = (r.get("href") or "").strip()
                    if not u:
                        continue
                    out.append((r.get("title", ""), u, r.get("body", "")))
                    if len(out) >= max_results:
                        break
            return out

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_run)
            return fut.result(timeout=timeout)
    except Exception:
        return search_bing_rss(query, max_results=max_results, timeout=timeout)


def process_web_search_fallback(conn, leaid, district_name, state, mapping, timeout):
    """Optional fallback if official site crawl fails."""
    cur = conn.cursor()
    queries = [
        f'"{district_name}" {state} math curriculum adopted',
        f'"{district_name}" {state} middle school math curriculum',
    ]

    inserted = 0
    for q in queries:
        for _, u, _ in search_ddg(q, max_results=4, timeout=max(4, timeout)):
            if not u:
                continue
            text, content_type, content_hash, _links = fetch_page(u, timeout=timeout)
            if not text:
                continue

            doc_id = upsert_document(cur, None, u, content_type, content_hash, text, "fetched")
            for ev in extract_evidence(
                text,
                district_name,
                source_url=u,
                trusted_domain=False,
            ):
                if insert_candidate(cur, leaid, "web_search", u, doc_id, ev, mapping):
                    inserted += 1

            if inserted >= 4:
                conn.commit()
                return inserted

    conn.commit()
    return inserted


def get_sources_to_process(
    conn,
    state,
    limit,
    min_schools,
    source_types,
    only_missing,
    random_sample=False,
    pending_only=False,
):
    """Load source rows prioritized by district size and missing coverage."""
    cur = conn.cursor()

    source_placeholders = ",".join("?" for _ in source_types)
    params = list(source_types)

    query = f"""
        SELECT s.id, s.leaid, d.district_name, d.state, s.source_type, s.url, s.domain
        FROM source_registry s
        JOIN districts d ON d.leaid = s.leaid
        WHERE s.source_type IN ({source_placeholders})
          AND COALESCE(d.school_count, 0) >= ?
    """
    params.append(min_schools)

    if state:
        query += " AND d.state = ?"
        params.append(state)

    if pending_only:
        query += " AND COALESCE(s.crawl_status, 'pending') = 'pending'"

    if only_missing:
        query += """
          AND (
              NOT EXISTS (
                  SELECT 1 FROM resolved_curriculum r1
                  WHERE r1.leaid = d.leaid AND r1.grade_band = 'k5'
              )
              OR NOT EXISTS (
                  SELECT 1 FROM resolved_curriculum r2
                  WHERE r2.leaid = d.leaid AND r2.grade_band = '68'
              )
          )
        """

    if random_sample:
        query += " ORDER BY RANDOM() LIMIT ?"
    else:
        query += " ORDER BY d.school_count DESC, d.state, d.district_name LIMIT ?"
    params.append(limit)

    cur.execute(query, params)
    return cur.fetchall()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=str, help="Process only this state")
    parser.add_argument("--limit", type=int, default=1000, help="Max source rows to process")
    parser.add_argument("--min-schools", type=int, default=1, help="Minimum school count")
    parser.add_argument(
        "--source-types",
        type=str,
        default="district_website",
        help="Comma-separated source types from source_registry",
    )
    parser.add_argument("--timeout", type=int, default=10, help="HTTP timeout seconds")
    parser.add_argument("--max-pages", type=int, default=4, help="Max pages per source")
    parser.add_argument("--only-missing", action="store_true", help="Only districts missing a grade band")
    parser.add_argument("--allow-web-search", action="store_true", help="Enable DDG fallback")
    parser.add_argument(
        "--site-search",
        action="store_true",
        help="Search only within district source domain when direct crawl misses",
    )
    parser.add_argument(
        "--browser-fallback",
        action="store_true",
        help="Use Playwright rendering for JS-heavy pages when plain HTTP fetch misses",
    )
    parser.add_argument(
        "--search-only",
        action="store_true",
        help="Skip source URL crawling and run only search-based discovery",
    )
    parser.add_argument(
        "--random-sample",
        action="store_true",
        help="Sample matching sources randomly instead of largest-first",
    )
    parser.add_argument(
        "--pending-only",
        action="store_true",
        help="Process only sources with crawl_status='pending'",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    mapping = load_normalization_map()

    source_types = [s.strip() for s in args.source_types.split(",") if s.strip()]
    sources = get_sources_to_process(
        conn,
        state=args.state,
        limit=args.limit,
        min_schools=args.min_schools,
        source_types=source_types,
        only_missing=args.only_missing,
        random_sample=args.random_sample,
        pending_only=args.pending_only,
    )

    print(f"Processing {len(sources)} source rows")
    print(f"  source_types={source_types}")
    if args.browser_fallback and not PLAYWRIGHT_AVAILABLE:
        print("  browser_fallback requested but playwright is not installed; continuing without it")
    if args.search_only:
        print("  mode=search_only")
    if args.random_sample:
        print("  sampling=random")
    if args.pending_only:
        print("  filter=pending_only")
    if args.state:
        print(f"  state={args.state}")

    start = time.time()
    found_sources = 0
    evidence_items = 0
    errors = 0

    for i, row in enumerate(sources, 1):
        source_id, leaid, district_name, state, source_type, url, _domain = row
        print(
            f"[{i}/{len(sources)}] {state} {district_name} [{source_type}]",
            end=" ",
            flush=True,
        )

        try:
            found = process_source(
                conn,
                row,
                mapping,
                timeout=args.timeout,
                max_pages=args.max_pages,
                site_search=args.site_search,
                browser_fallback=args.browser_fallback and PLAYWRIGHT_AVAILABLE,
                skip_source_crawl=args.search_only,
            )

            # Optional fallback: only when official source found nothing
            if found == 0 and args.allow_web_search:
                found += process_web_search_fallback(
                    conn,
                    leaid,
                    district_name,
                    state,
                    mapping,
                    timeout=args.timeout,
                )

            if found > 0:
                found_sources += 1
                evidence_items += found
                print(f"-> {found} evidence")
            else:
                print("-> no evidence")

        except Exception as exc:
            errors += 1
            cur = conn.cursor()
            mark_source(cur, source_id, "error")
            conn.commit()
            print(f"-> error: {exc}")

    duration = time.time() - start

    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO run_logs (run_date, phase, status, districts_processed,
                              districts_found, errors, duration_seconds, notes)
        VALUES (?, 'extraction', 'success', ?, ?, ?, ?, ?)
        """,
        (
            TODAY,
            len(sources),
            found_sources,
            errors,
            duration,
            (
                f"source_types={','.join(source_types)}, "
                f"allow_web_search={args.allow_web_search}, site_search={args.site_search}, "
                f"browser_fallback={args.browser_fallback and PLAYWRIGHT_AVAILABLE}, "
                f"search_only={args.search_only}"
            ),
        ),
    )
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM documents")
    doc_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM extraction_candidates")
    candidate_count = cur.fetchone()[0]

    print("\n=== Extraction Summary ===")
    print(f"Processed sources: {len(sources)}")
    print(f"Sources with evidence: {found_sources}")
    print(f"Evidence inserted: {evidence_items}")
    print(f"Errors: {errors}")
    print(f"Duration: {duration:.1f}s")
    print(f"Documents total: {doc_count}")
    print(f"Candidates total: {candidate_count}")

    conn.close()


if __name__ == "__main__":
    main()
