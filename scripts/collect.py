#!/usr/bin/env python3
"""Collect US plant-science graduate-position notices for the website.

Run from the repository root (the GitHub Action does this every day):

    python scripts/collect.py

Writes two files that the page and the workflow expect:

* data/opportunities.json  {"generated_at", "items": [...], "programs": []}
* data/source-health.json  one entry per source (ok / error / blocked)

Design rules
------------
* Only sources listed in SOURCES are read. Each source is a public feed or API.
* robots.txt is checked before every request, and the site is never hit
  faster than one request per second.
* Only the link, the title, a short excerpt (about 260 characters) and the
  facts the script can find (university, state, degree, deadline) are kept.
* If a source fails, its earlier entries are kept, so one bad day does not
  empty the list. If it works, its entries are replaced by the fresh set, so
  closed postings disappear.
* Entries already checked by hand in index.html ("opp-seed") are never
  overwritten by automatic ones.

To add a source, append a dict to SOURCES. Supported types are "wp-json"
(a WordPress REST posts endpoint) and "rss" (an RSS 2.0 feed).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable
from urllib import robotparser
from urllib.parse import urlparse

import requests
from defusedxml import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT_FILE = ROOT / "data" / "opportunities.json"
HEALTH_FILE = ROOT / "data" / "source-health.json"
INDEX_FILE = ROOT / "index.html"

USER_AGENT = (
    "MezanurRahmanSiteBot/1.0 "
    "(+https://mrahman-cmd.github.io/mezanurrahman-website/; plant-science graduate notices)"
)
TIMEOUT = 30
MAX_BYTES = 8_000_000
REQUEST_DELAY = 1.0
EXPIRY_GRACE_DAYS = 3  # the page hides a posting 3 days after its deadline
MAX_AGE_NO_DEADLINE_DAYS = 240
SNIPPET_CHARS = 260

SOURCES = [
    {
        "key": "wssa",
        "name": "WSSA job board",
        "type": "wp-json",
        "url": (
            "https://wssa.net/wp-json/wp/v2/posts"
            "?categories=17&per_page=100&_fields=id,date,link,title,excerpt,content"
        ),
    },
    {
        "key": "aspt",
        "name": "ASPT opportunities list",
        "type": "rss",
        "url": "https://www.aspt.net/news-blog/category/OPPORTUNITY?format=rss",
    },
]

STATE_NAMES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE",
    "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
    "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
    "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "District of Columbia": "DC",
}
STATE_CODES = set(STATE_NAMES.values())
_STATE_PATTERNS = [
    (name, re.compile(r"\b" + re.escape(name) + r"\b"))
    for name in sorted(STATE_NAMES, key=len, reverse=True)
]

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH = (
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)"
)
DATE_RES = [
    re.compile(_MONTH + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d)", re.I),
    re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH + r"\.?,?\s+(20\d\d)", re.I),
    re.compile(r"(\d{1,2})/(\d{1,2})/(20\d\d)"),
]

DEADLINE_KW = re.compile(
    r"(deadline|closing date|closes|apply by|applications? (?:are |is )?due|"
    r"due (?:on|by)|submit(?:ted)? by|must be received by|applications? accepted (?:until|through))",
    re.I,
)
REVIEW_KW = re.compile(
    r"(review of applications|applications? will be reviewed|begin(?:s)? "
    r"(?:reviewing|review)|consideration (?:begins|will begin)|screening of applications|"
    r"review(?:ing)? (?:of applications )?(?:begins|will begin))",
    re.I,
)
ROLLING_RE = re.compile(
    r"(until (?:the position is |the positions are )?filled|rolling (?:basis|review|admission)|"
    r"open until filled)",
    re.I,
)
POSTDOC_RE = re.compile(r"post-?\s?doc", re.I)
# "MS" as a degree: not part of "LC-MS" and not a state code after a comma ("Starkville, MS").
MS_RE = re.compile(r"(?<![\w-])(?<!, )M\.?S\.?(?![\w-])")
MASTERS_RE = re.compile(r"\bmaster'?s\b", re.I)
GRAD_TITLE_RE = re.compile(
    r"(graduate|ph\.?\s?d|doctoral|\bM\.?S\.?\s+(?:student|assistantship|position)|"
    r"master'?s|assistantship|grad student)",
    re.I,
)
GRAD_BODY_RE = re.compile(
    r"(graduate (?:research |teaching )?assistantship|graduate student|"
    r"ph\.?\s?d\.? (?:student|position|assistantship|program|opportunit)|"
    r"doctoral (?:student|position|program)|M\.?S\.? (?:student|assistantship)|"
    r"master'?s (?:student|position|degree)|prospective (?:graduate )?students)",
    re.I,
)
STAFF_TITLE_RE = re.compile(
    r"\b(professor|lecturer|faculty|director|department head|head of|chair|"
    r"technician|specialist|coordinator|administrative|manager|research associate|"
    r"scientist|agronomist|instructor|extension|curator|dean|postdoc\w*)\b",
    re.I,
)
PLANT_RE = re.compile(
    r"\b(plants?|crops?|agronom\w*|weeds?|weed science|soils?|horticult\w*|forest\w*|"
    r"trees?|botan\w*|genom\w*|breeding|pathology|turf\w*|seeds?|ecophysiolog\w*|"
    r"photosynth\w*|rhizospher\w*|phenotyp\w*|agricultur\w*|agroecolog\w*|poplar|"
    r"populus|biofuel|bioenergy|fung\w*|mycolog\w*|entomolog\w*|wheat|corn|maize|"
    r"soybeans?|rice|barley|sorghum|cotton|potato\w*|tomato\w*|onions?|garlic|fruits?|"
    r"vegetables?|grass\w*|grapes?|apples?|orchards?|forage|pasture|canola|fusarium|"
    r"cereals?|legumes?|pollinat\w*|juglans|nuts?)\b",
    re.I,
)

TOPICS = [
    ("Weed science", re.compile(r"\bweeds?\b|herbicide", re.I)),
    ("Plant pathology", re.compile(r"pathogen|pathology|plant disease", re.I)),
    ("Plant-microbe interactions", re.compile(r"microbi|rhizospher|symbio|mycorrhiz", re.I)),
    ("Molecular biology", re.compile(r"molecular|CRISPR|gene editing", re.I)),
    ("Genomics", re.compile(r"genom|GWAS|sequencing", re.I)),
    ("Phenotyping", re.compile(r"phenotyp", re.I)),
    ("Machine learning", re.compile(r"machine learning|deep learning|artificial intelligence", re.I)),
    ("Abiotic stress", re.compile(r"drought|heat stress|salinity|abiotic|stress tolerance", re.I)),
    ("Plant breeding", re.compile(r"breeding|cultivar", re.I)),
    ("Plant physiology", re.compile(r"physiolog|photosynth", re.I)),
    ("Root biology", re.compile(r"\broots?\b", re.I)),
    ("Soil health", re.compile(r"soil", re.I)),
    ("Horticulture", re.compile(r"horticult", re.I)),
    ("Forest genetics", re.compile(r"forest|\btrees?\b", re.I)),
    ("Cover crops", re.compile(r"cover crop", re.I)),
    ("Climate", re.compile(r"climate", re.I)),
]

# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "br", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _TextExtractor()
    parser.feed(markup or "")
    parser.close()
    text = html.unescape("".join(parser.parts))
    return re.sub(r"\s+", " ", text).strip()


def make_snippet(text: str, title: str) -> str:
    body = text
    if body.lower().startswith(title.lower()):
        body = body[len(title):].lstrip(" -:.")
    body = body.strip()
    if len(body) <= SNIPPET_CHARS:
        return body
    cut = body[:SNIPPET_CHARS]
    stop = max(cut.rfind(". "), cut.rfind("; "))
    if stop > SNIPPET_CHARS * 0.5:
        return cut[: stop + 1]
    return cut[: cut.rfind(" ")].rstrip(",;:") + "…"


def normalise_url(url: str) -> str:
    parts = urlparse(url.strip())
    path = parts.path.rstrip("/") or "/"
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}"


def is_http_url(url: str) -> bool:
    try:
        return urlparse(url).scheme in ("http", "https") and bool(urlparse(url).netloc)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------


def find_dates(text: str) -> list[tuple[int, int, dt.date]]:
    found: list[tuple[int, int, dt.date]] = []
    for index, pattern in enumerate(DATE_RES):
        for match in pattern.finditer(text):
            try:
                if index == 0:
                    month = MONTHS[match.group(1)[:3].lower()]
                    day, year = int(match.group(2)), int(match.group(3))
                elif index == 1:
                    day, year = int(match.group(1)), int(match.group(3))
                    month = MONTHS[match.group(2)[:3].lower()]
                else:
                    month, day, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
                found.append((match.start(), match.end(), dt.date(year, month, day)))
            except (ValueError, KeyError):
                continue
    found.sort()
    return found


def extract_deadline(text: str) -> tuple[str | None, str | None]:
    """Return (ISO date, kind) where kind is 'deadline' or 'review'."""
    dates = find_dates(text)
    if not dates:
        return None, None
    for pattern, kind in ((DEADLINE_KW, "deadline"), (REVIEW_KW, "review")):
        for match in pattern.finditer(text):
            for start, _end, date in dates:
                if match.start() <= start <= match.end() + 120:
                    return date.isoformat(), kind
    return None, None


def _degree_flags(probe: str) -> tuple[bool, bool]:
    phd = bool(re.search(r"\bph\.?\s?d\b|\bdoctoral\b", POSTDOC_RE.sub(" ", probe), re.I))
    ms = bool(MS_RE.search(probe) or MASTERS_RE.search(probe))
    return phd, ms


def extract_degree(title: str, text: str) -> str:
    """Degree offered. The title wins; the body is used only when the title is silent,
    because bodies often say "applicants need a BS or MS" (an entry requirement)."""
    phd, ms = _degree_flags(title)
    if not (phd or ms):
        phd, ms = _degree_flags(text[:1500])
    if phd and ms:
        return "PhD / MS"
    if phd:
        return "PhD"
    if ms:
        return "MS"
    return ""


def extract_start_term(text: str) -> str:
    match = re.search(r"\b(Spring|Summer|Fall|Autumn|Winter)\s+(20\d\d)\b", text)
    return f"{match.group(1)} {match.group(2)}" if match else ""


def extract_state(title: str, text: str) -> str:
    probe = f"{title}. {text[:2500]}"
    work = probe
    best: tuple[int, str] | None = None
    for name, pattern in _STATE_PATTERNS:
        match = pattern.search(work)
        if match:
            if best is None or match.start() < best[0]:
                best = (match.start(), STATE_NAMES[name])
            # blank the match so "Virginia" does not also match "West Virginia"
            work = work[: match.start()] + " " * (match.end() - match.start()) + work[match.end():]
    code_match = re.search(r",\s*([A-Z]{2})\b(?:\s+\d{5})?", probe)
    if code_match and code_match.group(1) in STATE_CODES:
        if best is None or code_match.start() < best[0]:
            return code_match.group(1)
    return best[1] if best else ""


_UNI_STOP = {"The", "This", "Our", "At", "In", "Of", "And", "A", "An", "For", "With", "At", "By"}


def extract_university(title: str, text: str) -> str:
    probe = f"{title}. {text[:2500]}"
    candidates: list[tuple[int, str]] = []
    match = re.search(
        r"\b(University of [A-Z][a-z]+(?:[ -][A-Z][a-z]+){0,2}"
        r"(?: at [A-Z][a-z]+(?: [A-Z][a-z]+)?)?)",
        probe,
    )
    if match:
        candidates.append((match.start(), match.group(1)))
    match = re.search(r"\b((?:[A-Z][A-Za-z&.]*\s){1,3}(?:State\s)?University)\b", probe)
    if match:
        words = match.group(1).split()
        while words and words[0] in _UNI_STOP:
            words.pop(0)
        if len(words) >= 2:
            candidates.append((match.start(), " ".join(words)))
    if not candidates:
        return ""
    return min(candidates)[1].strip(" .,;")


def extract_topics(title: str, text: str) -> list[str]:
    probe = f"{title}. {text[:3000]}"
    return [label for label, pattern in TOPICS if pattern.search(probe)][:4]


def is_relevant(title: str, text: str) -> bool:
    """True for plant-science graduate positions (not staff, faculty or postdoc roles)."""
    if POSTDOC_RE.search(title):
        return False
    title_grad = bool(GRAD_TITLE_RE.search(title) or MS_RE.search(title))
    if not title_grad:
        if STAFF_TITLE_RE.search(title):
            return False
        if not GRAD_BODY_RE.search(text[:1500]):
            return False
    if PLANT_RE.search(title):
        return True
    return len(PLANT_RE.findall(text[:3000])) >= 2


def build_item(raw: dict, source: dict, today: dt.date) -> dict | None:
    title = html_to_text(raw.get("title", ""))
    url = (raw.get("url") or "").strip()
    text = html_to_text(raw.get("body_html", ""))
    if not title or not is_http_url(url):
        return None
    if not is_relevant(title, text):
        return None

    deadline, kind = extract_deadline(text)
    rolling = bool(ROLLING_RE.search(text))
    item = {
        "id": f"{source['key']}-{hashlib.sha1(normalise_url(url).encode()).hexdigest()[:10]}",
        "title": title,
        "url": url,
        "source": source["name"],
        "source_key": source["key"],
        "university": extract_university(title, text),
        "state": extract_state(title, text),
        "degree": extract_degree(title, text),
        "topics": extract_topics(title, text),
        "posted": raw.get("posted") or "",
        "deadline": deadline or "",
        "deadline_kind": kind or "",
        "start_term": extract_start_term(text),
        "snippet": make_snippet(text, title),
        "first_seen": today.isoformat(),
        "checked": today.isoformat(),
        "rolling": rolling,
    }
    return item


def is_expired(item: dict, today: dt.date) -> bool:
    deadline = item.get("deadline")
    if deadline and item.get("deadline_kind") == "deadline":
        try:
            if (dt.date.fromisoformat(deadline) - today).days < -EXPIRY_GRACE_DAYS:
                return True
        except ValueError:
            pass
    elif not deadline:
        posted = item.get("posted") or item.get("first_seen")
        try:
            if posted and (today - dt.date.fromisoformat(posted)).days > MAX_AGE_NO_DEADLINE_DAYS:
                return True
        except ValueError:
            pass
    return False


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


class SourceError(Exception):
    """A source could not be read; the message is shown in source-health.json."""


_robots_cache: dict[str, robotparser.RobotFileParser | None] = {}


def _get(url: str, session: requests.Session) -> requests.Response:
    last: Exception | None = None
    for attempt in range(3):
        try:
            response = session.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
                timeout=TIMEOUT,
                stream=True,
            )
            if response.status_code >= 500:
                raise SourceError(f"HTTP {response.status_code}")
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    response.close()
                    raise SourceError("response larger than the safety limit")
                chunks.append(chunk)
            response._content = b"".join(chunks)  # noqa: SLF001
            response._content_consumed = True  # noqa: SLF001
            return response
        except (requests.RequestException, SourceError) as error:
            last = error
            time.sleep(2 * (attempt + 1))
    raise SourceError(f"could not fetch ({last})")


def robots_allows(url: str, session: requests.Session) -> bool:
    parts = urlparse(url)
    root = f"{parts.scheme}://{parts.netloc}"
    if root not in _robots_cache:
        parser = robotparser.RobotFileParser()
        try:
            response = _get(f"{root}/robots.txt", session)
            if response.status_code in (401, 403) or response.status_code >= 500:
                parser = None  # type: ignore[assignment]
            elif response.status_code >= 400:
                parser.parse([])  # no robots.txt: everything allowed
            else:
                parser.parse(response.text.splitlines())
        except SourceError:
            parser = None  # type: ignore[assignment]
        _robots_cache[root] = parser
    parser = _robots_cache[root]
    return bool(parser and parser.can_fetch(USER_AGENT, url))


def parse_wp_json(payload: list) -> list[dict]:
    records = []
    for post in payload:
        if not isinstance(post, dict):
            continue
        content = (post.get("content") or {}).get("rendered") or (post.get("excerpt") or {}).get("rendered", "")
        records.append(
            {
                "title": (post.get("title") or {}).get("rendered", ""),
                "url": post.get("link", ""),
                "posted": str(post.get("date", ""))[:10],
                "body_html": content,
            }
        )
    return records


def parse_rss(xml_text: str) -> list[dict]:
    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    records = []
    for item in root.iter("item"):
        pub = (item.findtext("pubDate") or "").strip()
        posted = ""
        match = re.search(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(20\d\d)", pub)
        if match and match.group(2).lower() in MONTHS:
            try:
                posted = dt.date(int(match.group(3)), MONTHS[match.group(2).lower()], int(match.group(1))).isoformat()
            except ValueError:
                posted = ""
        body = item.findtext("content:encoded", namespaces=ns) or item.findtext("description") or ""
        records.append(
            {
                "title": item.findtext("title") or "",
                "url": (item.findtext("link") or "").strip(),
                "posted": posted,
                "body_html": body,
            }
        )
    return records


def fetch_source(source: dict, session: requests.Session) -> list[dict]:
    url = source["url"]
    if not robots_allows(url, session):
        raise SourceError("blocked: robots.txt does not allow this request")
    time.sleep(REQUEST_DELAY)
    response = _get(url, session)
    if response.status_code >= 400:
        raise SourceError(f"HTTP {response.status_code}")
    try:
        if source["type"] == "wp-json":
            return parse_wp_json(response.json())
        if source["type"] == "rss":
            return parse_rss(response.text)
    except (ValueError, ET.ParseError) as error:
        raise SourceError(f"could not read the response ({error})") from error
    raise SourceError(f"unknown source type {source['type']!r}")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def collect(
    sources: Iterable[dict],
    fetch: Callable[[dict], list[dict]],
    today: dt.date,
) -> tuple[list[dict], list[dict], set[str]]:
    """Fetch every source. Returns (items, health entries, keys of sources that worked)."""
    items: list[dict] = []
    health: list[dict] = []
    ok_keys: set[str] = set()
    for source in sources:
        entry = {"key": source["key"], "name": source["name"], "url": source["url"],
                 "status": "ok", "fetched": 0, "kept": 0, "error": ""}
        try:
            raw = fetch(source)
            entry["fetched"] = len(raw)
            built = [item for item in (build_item(r, source, today) for r in raw) if item]
            entry["kept"] = len(built)
            items.extend(built)
            ok_keys.add(source["key"])
        except SourceError as error:
            entry["status"] = "blocked" if str(error).startswith("blocked") else "error"
            entry["error"] = str(error)
        health.append(entry)
    return items, health, ok_keys


def merge(
    previous: list[dict],
    fresh: list[dict],
    ok_keys: set[str],
    seed_urls: set[str],
    today: dt.date,
) -> list[dict]:
    first_seen = {normalise_url(p["url"]): p.get("first_seen") for p in previous if p.get("url")}
    merged: dict[str, dict] = {}

    for item in fresh:
        key = normalise_url(item["url"])
        if key in seed_urls:
            continue
        item["first_seen"] = first_seen.get(key) or item["first_seen"]
        merged[key] = item

    for old in previous:  # keep earlier entries from sources that failed today
        key = normalise_url(old.get("url", ""))
        if old.get("source_key") not in ok_keys and key and key not in merged and key not in seed_urls:
            merged[key] = old

    result = [item for item in merged.values() if not is_expired(item, today)]
    result.sort(key=lambda i: (i.get("posted") or i.get("first_seen") or ""), reverse=True)
    return result


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_seed_urls(index_file: Path) -> set[str]:
    try:
        page = index_file.read_text(encoding="utf-8")
    except OSError:
        return set()
    match = re.search(r'<script type="application/json" id="opp-seed">(.*?)</script>', page, re.S)
    if not match:
        return set()
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return set()
    urls = {normalise_url(i["url"]) for i in data.get("items", []) if i.get("url")}
    urls |= {normalise_url(p["url"]) for p in data.get("programs", []) if p.get("url")}
    return urls


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()
    previous_data = read_json(OUT_FILE, {})
    previous = previous_data.get("items", []) if isinstance(previous_data, dict) else []

    session = requests.Session()
    items, health, ok_keys = collect(SOURCES, lambda s: fetch_source(s, session), today)
    merged = merge(previous, items, ok_keys, read_seed_urls(INDEX_FILE), today)

    stamp = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    write_json(OUT_FILE, {"generated_at": stamp, "items": merged, "programs": []})
    write_json(HEALTH_FILE, {"generated_at": stamp, "sources": health})

    for entry in health:
        line = f"{entry['name']}: {entry['status']}, {entry['fetched']} read, {entry['kept']} kept"
        print(line + (f" ({entry['error']})" if entry["error"] else ""))
        if entry["status"] != "ok":
            print(f"::warning::{entry['name']} failed: {entry['error']}")
    print(f"Wrote {len(merged)} positions to {OUT_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
