#!/usr/bin/env python3
"""Add new journal articles from ORCID to the website, without touching the hand-checked list.

How it works
  1. Reads the public ORCID record and keeps journal articles that have a DOI.
  2. Ignores any DOI (or title) that is already on the page outside the automatic block.
  3. Looks up authors, journal and year for each new DOI in Crossref.
  4. Writes the new articles into a "Recently added" group between the two markers in index.html
         <!-- AUTO-PUBS-START --> ... <!-- AUTO-PUBS-END -->
     and keeps a copy in data/new-publications.json.
  5. Updates the article totals shown on the page.

Only the first-author tag is added automatically (from the author order); a "Last author"
tag likewise. Corresponding-author status is NOT known and is never guessed. When you have checked a
paper, move it into its year group by hand: it disappears from the automatic group by itself.

To hide a paper permanently, add its DOI to data/publications-ignore.json: {"ignore": ["10.xxxx/yyyy"]}
"""

import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"
CACHE = ROOT / "data" / "new-publications.json"
IGNORE = ROOT / "data" / "publications-ignore.json"

ORCID = "0000-0001-8822-9683"
START, END = "<!-- AUTO-PUBS-START -->", "<!-- AUTO-PUBS-END -->"
MAX_NEW_PER_RUN = 25


# ----------------------------------------------------------------- network

def get_json(url, tries=3):
    request = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "mezanurrahman-website publication updater (GitHub Actions)",
    })
    last = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            last = error
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            last = error
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Could not fetch {url}: {last}")


# ------------------------------------------------------------------ helpers

def norm_doi(value):
    value = html.unescape(value or "").strip().lower()
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value)
    return value.rstrip(".,;)")


def norm_title(value):
    value = html.unescape(re.sub(r"<[^>]+>", "", value or "")).lower()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def esc(value):
    return html.escape(value, quote=True)


def clean_title(raw):
    """Crossref titles can carry <i>/<sub> markup: keep italics, drop the rest, escape the text."""
    text = html.unescape(raw or "")
    text = re.sub(r"</?(i|em)>", lambda m: "\x00" + ("/" if "/" in m.group(0) else ""), text)
    text = re.sub(r"<[^>]+>", "", text)
    text = esc(" ".join(text.split()))
    return re.sub(r"\x00(/?)", lambda m: f"<{m.group(1)}em>", text)


def plain_title(raw):
    return " ".join(re.sub(r"<[^>]+>", "", html.unescape(raw or "")).split())


def initials(given):
    parts = [p for p in re.split(r"[\s.\-]+", given or "") if p]
    return "".join(p[0].upper() for p in parts)


def is_me(author):
    if ORCID in (author.get("ORCID") or ""):
        return True
    return (author.get("family", "").strip().lower() == "rahman"
            and initials(author.get("given", "")) == "MM")


def fmt_authors(authors):
    out, me_index = [], None
    for i, a in enumerate(authors):
        family = (a.get("family") or a.get("name") or "").strip()
        if not family:
            continue
        name = f"{family} {initials(a.get('given'))}".strip()
        if is_me(a):
            me_index = i
            name = f"<strong>{esc(name)}</strong>"
        else:
            name = esc(name)
        out.append(name)
    return ", ".join(out), me_index


# ---------------------------------------------------------------- the page

def read_page():
    text = INDEX.read_text(encoding="utf-8")
    if START not in text or END not in text:
        sys.exit(f"{INDEX.name} has no {START} ... {END} markers. Upload the latest index.html first.")
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    return before, after


def page_keys(before, after):
    body = before + after
    dois = {norm_doi(m) for m in re.findall(r"doi\.org/(10\.[^\s\"'<>]+)", body)}
    titles = {norm_title(m) for m in re.findall(r"<h4>.*?</h4>", body, re.S)}
    curated = len(re.findall(r'class="publication"', body))
    return dois, titles, curated


def orcid_articles():
    data = get_json(f"https://pub.orcid.org/v3.0/{ORCID}/works")
    if not isinstance(data, dict) or "group" not in data:
        raise RuntimeError("Unexpected ORCID response")
    found = {}
    for group in data["group"]:
        for summary in group.get("work-summary", []):
            if summary.get("type") != "JOURNAL_ARTICLE":
                continue
            ids = (summary.get("external-ids") or {}).get("external-id") or []
            dois = [norm_doi(i.get("external-id-value")) for i in ids
                    if (i.get("external-id-type") or "").lower() == "doi"]
            dois = [d for d in dois if d.startswith("10.")]
            if not dois:
                continue
            title = (((summary.get("title") or {}).get("title") or {}).get("value")) or ""
            year = ((((summary.get("publication-date") or {}).get("year")) or {}).get("value")) or ""
            journal = ((summary.get("journal-title") or {}).get("value")) or ""
            found.setdefault(dois[0], {"doi": dois[0], "title": title, "year": year, "journal": journal})
    return found


def crossref(doi):
    data = get_json("https://api.crossref.org/works/" + urllib.request.quote(doi, safe="/()"))
    message = (data or {}).get("message") or {}
    if not message:
        return None
    date = None
    for key in ("published-print", "published-online", "published", "issued"):
        parts = (message.get(key) or {}).get("date-parts") or [[None]]
        if parts and parts[0] and parts[0][0]:
            date = parts[0][0]
            break
    return {
        "title": (message.get("title") or [""])[0],
        "journal": (message.get("container-title") or [""])[0],
        "year": str(date) if date else "",
        "type": message.get("type", ""),
        "authors": [{"given": a.get("given", ""), "family": a.get("family", ""),
                     "name": a.get("name", ""), "ORCID": a.get("ORCID", "")}
                    for a in (message.get("author") or [])],
    }


def build_entry(candidate):
    entry = {"doi": candidate["doi"], "title": candidate["title"],
             "journal": candidate["journal"], "year": candidate["year"], "authors": []}
    meta = crossref(candidate["doi"])
    if meta:
        if meta["type"] and meta["type"] != "journal-article":
            return None
        for key in ("title", "journal", "year", "authors"):
            if meta[key]:
                entry[key] = meta[key]
    return entry


def render_article(entry):
    authors_html, me_index = fmt_authors(entry.get("authors", []))
    n = len([a for a in entry.get("authors", []) if (a.get("family") or a.get("name"))])
    tag, roles = "", ""
    if me_index == 0 and n > 1:
        tag, roles = '<span class="tag">First author</span>', "first"
    elif me_index is not None and me_index == n - 1 and n > 1:
        tag = '<span class="tag">Last author</span>'
    year = esc(str(entry.get("year", "")))
    journal = esc(entry.get("journal", ""))
    venue = f"<em>{journal}</em> &middot; {year}" if journal and year else (journal or year)
    lines = [
        f'          <article class="publication" data-roles="{roles}" data-auto="1">',
        f'            <h4><a href="https://doi.org/{esc(entry["doi"])}" target="_blank" rel="noopener noreferrer">{clean_title(entry["title"])}</a></h4>',
    ]
    if authors_html:
        lines.append(f'            <p class="authors">{authors_html}</p>')
    if venue:
        lines.append(f'            <p class="pub-venue">{venue}</p>')
    if tag:
        lines.append(f"            {tag}")
    lines.append("          </article>")
    return "\n".join(lines)


def render_block(entries):
    if not entries:
        return f"{START}\n        {END}"
    entries = sorted(entries, key=lambda e: (str(e.get("year", "")), plain_title(e["title"]).lower()), reverse=True)
    articles = "\n".join(render_article(e) for e in entries)
    return f"""{START}
        <details class="pub-group" id="pub-new" open>
          <summary><h3 class="pub-year">Recently added <span class="pub-count">({len(entries)})</span></h3></summary>
          <p class="pub-new-note">Added automatically from my ORCID record. First- and last-author tags follow the author order; corresponding-author status has not yet been checked.</p>
{articles}
        </details>
        {END}"""


def update_counts(page, total):
    page = re.sub(r"\d+(\s+journal articles from my ORCID record)", lambda m: f"{total}{m.group(1)}", page, count=1)
    page = re.sub(r'(<span class="metric-number">)\d+(</span>\s*<p>Journal articles \(ORCID record\))',
                  lambda m: f"{m.group(1)}{total}{m.group(2)}", page, count=1)
    page = re.sub(r"Showing \d+ of \d+ articles", f"Showing {total} of {total} articles", page, count=1)
    return page


def main():
    before, after = read_page()
    known_dois, known_titles, curated = page_keys(before, after)

    ignore = set()
    if IGNORE.exists():
        try:
            ignore = {norm_doi(d) for d in json.loads(IGNORE.read_text(encoding="utf-8")).get("ignore", [])}
        except (ValueError, AttributeError):
            print("::warning::data/publications-ignore.json is not valid and was ignored")

    cache = {}
    if CACHE.exists():
        try:
            cache = {norm_doi(e["doi"]): e for e in json.loads(CACHE.read_text(encoding="utf-8")).get("articles", [])}
        except (ValueError, KeyError, TypeError):
            cache = {}

    try:
        orcid = orcid_articles()
    except RuntimeError as error:
        sys.exit(f"ORCID could not be read, nothing was changed: {error}")

    fetched = 0
    for doi, candidate in orcid.items():
        if doi in known_dois or doi in ignore or doi in cache:
            continue
        if norm_title(candidate["title"]) in known_titles:
            continue
        if fetched >= MAX_NEW_PER_RUN:
            print("Limit reached for this run; the rest will be added next time")
            break
        try:
            entry = build_entry(candidate)
        except RuntimeError as error:
            print(f"::warning::{doi}: {error}")
            continue
        fetched += 1
        if entry:
            cache[doi] = entry
            print(f"New article: {doi}")

    live = [e for d, e in cache.items()
            if d in orcid and d not in known_dois and d not in ignore
            and norm_title(e["title"]) not in known_titles]

    page = before + render_block(live) + after
    page = update_counts(page, curated + len(live))

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"articles": sorted(live, key=lambda e: e["doi"])}, indent=2, ensure_ascii=False) + "\n",
                     encoding="utf-8")
    if page != INDEX.read_text(encoding="utf-8"):
        INDEX.write_text(page, encoding="utf-8")
        print(f"index.html updated: {len(live)} recently added, {curated + len(live)} in total")
    else:
        print("No changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

