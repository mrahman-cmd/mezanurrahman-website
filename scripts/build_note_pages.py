#!/usr/bin/env python3
"""Build a web page for every published research note.

Notes are written in data/notes.json (see UPDATING.md). This script, run by the
"Build note pages" workflow, turns each published note into its own page so that
it has its own address, title and description and can be found by search engines:

    notes/index.html                 list of all notes
    notes/<note-id>/index.html       one page per note
    notes/notes.css                  shared style (fonts are copied from index.html)
    404.html                         page shown for a wrong address
    sitemap.xml                      home page + notes index + every note

A note appears only if it is "published", dated today or earlier (UTC) and well
formed, exactly the rules the home page uses. Nothing else in the repository is
changed. Everything in the notes/ folder is rebuilt each time, so do not keep
other files there.
"""

import html
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "notes.json"
INDEX = ROOT / "index.html"
NOTES_DIR = ROOT / "notes"
SITEMAP = ROOT / "sitemap.xml"
NOT_FOUND = ROOT / "404.html"

SITE = "https://mrahman-cmd.github.io/mezanurrahman-website/"
AUTHOR = "Dr. Md. Mezanur Rahman"
ORCID = "https://orcid.org/0000-0001-8822-9683"
PORTRAIT = SITE + "Md_Mezanur_Rahman-466.jpg"
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")
INLINE = re.compile(r"\*([^*]+)\*|\[([^\]]+)\]\(([^)\s]+)\)")


# ---------------------------------------------------------------- validation

def is_text(value, maximum):
    return isinstance(value, str) and value.strip() != "" and len(value) <= maximum


def valid_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        return False


def valid_block(block):
    if not isinstance(block, dict):
        return False
    kind = block.get("type")
    if kind in ("p", "refs"):
        return is_text(block.get("text"), 6000)
    if kind == "list":
        items = block.get("items")
        return (isinstance(items, list) and 0 < len(items) <= 20
                and all(is_text(i, 1500) for i in items)
                and ("intro" not in block or is_text(block["intro"], 300)))
    return False


def usable(note, today):
    return (isinstance(note, dict)
            and note.get("status") == "published"
            and isinstance(note.get("id"), str) and ID_RE.match(note["id"])
            and valid_date(note.get("date")) and note["date"] <= today
            and is_text(note.get("title"), 200)
            and ("tag" not in note or is_text(note["tag"], 60))
            and isinstance(note.get("blocks"), list)
            and 0 < len(note["blocks"]) <= 40
            and all(valid_block(b) for b in note["blocks"]))


def load_notes():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        data = json.loads(DATA.read_text(encoding="utf-8"))
        raw = data.get("notes", []) if isinstance(data, dict) else []
    except (OSError, ValueError) as error:
        print(f"::warning::{DATA.name} could not be read: {error}")
        raw = []

    notes, seen = [], set()
    for note in raw:
        if usable(note, today) and note["id"] not in seen:
            seen.add(note["id"])
            notes.append(note)
        else:
            name = note.get("id", "?") if isinstance(note, dict) else "?"
            print(f"Skipped note {name} (not published yet, dated in the future, or not valid)")
    notes.sort(key=lambda n: (n["date"], n["id"]), reverse=True)
    return notes


# ------------------------------------------------------------------ helpers

def esc(text):
    return html.escape(text, quote=True)


def safe_http(value):
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    return value if parsed.scheme in ("http", "https") and parsed.netloc else None


def inline(text):
    """*italic* and [text](https://link) only; everything else is escaped."""
    out, last = [], 0
    for match in INLINE.finditer(text):
        out.append(esc(text[last:match.start()]))
        if match.group(1) is not None:
            out.append(f"<em>{esc(match.group(1))}</em>")
        else:
            href = safe_http(match.group(3))
            if href:
                out.append(f'<a href="{esc(href)}" target="_blank" rel="noopener noreferrer">{esc(match.group(2))}</a>')
            else:
                out.append(esc(match.group(2)))
        last = match.end()
    out.append(esc(text[last:]))
    return "".join(out)


def plain(text):
    return INLINE.sub(lambda m: m.group(1) or m.group(2), text)


def pretty_date(iso):
    year, month, day = iso.split("-")
    return f"{int(day)} {MONTHS[int(month) - 1]} {year}"


def first_paragraph(note):
    for block in note["blocks"]:
        if block["type"] == "p":
            return plain(block["text"])
    return plain(note["title"])


def shorten(text, limit):
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "…"


def fonts_css():
    """Copy the @font-face rules from index.html so the pages use the same fonts."""
    try:
        text = INDEX.read_text(encoding="utf-8")
        start = text.index("<style>") + len("<style>")
        end = text.index(":root", start)
        css = text[start:end]
        return css if "@font-face" in css else ""
    except (OSError, ValueError):
        return ""


PAGE_CSS = """
:root {
  --navy: #07414d; --navy-dark: #04303a; --blue-dark: #066147; --light-blue: #e4f3ee;
  --orange-light: #00b38e; --white: #ffffff; --off-white: #f5faf7; --text: #20393d;
  --muted: #4f6b66; --border: #c6ddd3; --shadow: 0 8px 22px rgba(7, 65, 77, 0.10);
  --serif: "Marcellus SC", Georgia, "Times New Roman", serif;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body { background: var(--off-white); color: var(--text); font-family: "Montserrat", Arial, Helvetica, sans-serif;
  font-size: 16px; line-height: 1.7; min-height: 100vh; display: flex; flex-direction: column; }
h1, h2, h3 { font-family: var(--serif); letter-spacing: 0.02em; color: var(--navy-dark); }
a { color: var(--blue-dark); font-weight: 600; text-underline-offset: 3px; }
a:hover { color: var(--navy); }
.skip { background: #fff; left: 8px; padding: 8px 12px; position: absolute; top: -60px; z-index: 10; }
.skip:focus { top: 8px; }
header { background: var(--navy); border-bottom: 3px solid var(--orange-light); }
nav { align-items: center; display: flex; flex-wrap: wrap; gap: 8px 22px; justify-content: space-between;
  margin: auto; padding: 14px 0; width: min(1100px, 92%); }
.brand { color: #fff; font-weight: 800; text-decoration: none; font-size: 1.05rem; }
.brand small { color: #bfe3d6; display: block; font-size: 0.66rem; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; }
.links { display: flex; flex-wrap: wrap; gap: 6px 18px; list-style: none; }
.links a { color: #fff; font-size: 0.9rem; font-weight: 700; text-decoration: none; }
.links a:hover { color: #7fe3c6; }
main { flex: 1; margin: auto; padding: 44px 0 70px; width: min(860px, 92%); }
.crumbs { color: var(--muted); font-size: 0.9rem; margin-bottom: 18px; }
.meta { align-items: center; color: var(--muted); display: flex; flex-wrap: wrap; font-size: 0.9rem;
  font-weight: 700; gap: 6px 12px; margin-bottom: 10px; }
.tag { background: var(--light-blue); border-radius: 999px; color: var(--blue-dark); font-size: 0.78rem;
  letter-spacing: 0.06em; padding: 2px 10px; text-transform: uppercase; }
h1 { font-size: clamp(1.7rem, 4vw, 2.5rem); line-height: 1.25; margin-bottom: 22px; }
article p, article ul { margin-top: 14px; }
article ul { margin-left: 1.3rem; }
article li { margin-bottom: 6px; }
.refs { border-top: 1px solid var(--border); color: var(--muted); font-size: 0.94rem; margin-top: 28px; padding-top: 14px; }
.about { background: #fff; border-left: 5px solid var(--orange-light); box-shadow: var(--shadow); margin-top: 34px; padding: 16px 20px; }
.about p { margin: 0; }
.pager { display: flex; flex-wrap: wrap; gap: 12px 30px; justify-content: space-between; margin-top: 34px; }
.list { list-style: none; margin-top: 26px; }
.list li { background: #fff; border-left: 5px solid var(--orange-light); box-shadow: var(--shadow); margin-bottom: 18px; padding: 20px 24px; }
.list h2 { font-size: 1.35rem; line-height: 1.3; margin: 6px 0 8px; }
.list h2 a { color: var(--navy-dark); text-decoration: none; }
.list h2 a:hover { text-decoration: underline; }
.list p { margin: 0; }
.lead { color: var(--muted); margin-top: 14px; }
footer { background: var(--navy-dark); color: #c0dcd2; font-size: 0.9rem; padding: 26px 5%; text-align: center; }
footer p + p { font-size: 0.82rem; margin: 8px auto 0; max-width: 780px; }
footer a { color: #7fe3c6; }
@media (min-width: 1700px) {
  html { font-size: min(1vw, 44px); }
  body { font-size: 1rem; }
  nav { width: min(80rem, 92%); }
  main { width: min(54rem, 92%); }
}
@media print { header, footer, .pager, .skip { display: none; } body { background: #fff; } .about { box-shadow: none; } }
"""


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def head(title, description, url, css_href, extra="", robots=None):
    robots_tag = f'<meta name="robots" content="{robots}">\n' if robots else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
{robots_tag}<meta name="theme-color" content="#07414d">
<link rel="canonical" href="{esc(url)}">
<meta property="og:type" content="{'article' if '/notes/' in url and not url.endswith('/notes/') else 'website'}">
<meta property="og:site_name" content="{esc(AUTHOR)}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(url)}">
<meta property="og:image" content="{PORTRAIT}">
<meta name="twitter:card" content="summary">
<meta name="twitter:image" content="{PORTRAIT}">
{extra}<link rel="stylesheet" href="{css_href}">
</head>
"""


def header_html(home):
    return f"""<body>
<a class="skip" href="#main">Skip to main content</a>
<header>
  <nav aria-label="Primary">
    <a class="brand" href="{home}">{esc(AUTHOR)}<small>Plant scientist &middot; combined stress biology &middot; plant phenotyping</small></a>
    <ul class="links">
      <li><a href="{home}">Home</a></li>
      <li><a href="{home}notes/">Research notes</a></li>
      <li><a href="{home}#publications">Publications</a></li>
      <li><a href="{home}#contact">Contact</a></li>
    </ul>
  </nav>
</header>
"""


def footer_html():
    year = datetime.now(timezone.utc).year
    return f"""<footer>
  <p>&copy; {year} {esc(AUTHOR)} &middot; Plant Scientist &middot; Combined Abiotic Stress Biology</p>
  <p>Views expressed on this website are my own and do not represent Oak Ridge National Laboratory, UT-Battelle, LLC, or the U.S. Department of Energy.</p>
</footer>
</body>
</html>
"""


# --------------------------------------------------------------------- pages

def note_page(note, newer, older):
    url = f"{SITE}notes/{note['id']}/"
    description = shorten(first_paragraph(note), 155)
    title = f"{note['title']} | Research notes | {AUTHOR}"
    ld = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": note["title"],
        "description": description,
        "datePublished": note["date"],
        "dateModified": note["date"],
        "inLanguage": "en-GB",
        "url": url,
        "mainEntityOfPage": url,
        "isAccessibleForFree": True,
        "author": {"@type": "Person", "name": AUTHOR, "url": SITE, "sameAs": [ORCID]},
        "isPartOf": {"@type": "Blog", "name": "Research notes", "url": SITE + "notes/"},
    }
    if note.get("tag"):
        ld["articleSection"] = note["tag"]
    extra = '<script type="application/ld+json">' + json.dumps(ld, ensure_ascii=False).replace("</", "<\\/") + "</script>\n"

    body = []
    for block in note["blocks"]:
        if block["type"] == "list":
            if block.get("intro"):
                body.append(f"<p>{esc(block['intro'])}</p>")
            items = "".join(f"<li>{inline(i)}</li>" for i in block["items"])
            body.append(f"<ul>{items}</ul>")
        elif block["type"] == "refs":
            body.append(f'<p class="refs">{inline(block["text"])}</p>')
        else:
            body.append(f"<p>{inline(block['text'])}</p>")

    tag = f'<span class="tag">{esc(note["tag"])}</span>' if note.get("tag") else ""
    pager = []
    if newer:
        pager.append(f'<a href="../{newer["id"]}/" rel="next">Newer: {esc(newer["title"])}</a>')
    if older:
        pager.append(f'<a href="../{older["id"]}/" rel="prev">Older: {esc(older["title"])}</a>')
    pager_html = f'<nav class="pager" aria-label="Other notes">{"".join(pager)}</nav>' if pager else ""

    return (head(title, description, url, "../notes.css", extra)
            + header_html("../../")
            + f"""<main id="main">
  <p class="crumbs"><a href="../../">Home</a> &rsaquo; <a href="../">Research notes</a></p>
  <article>
    <div class="meta"><time datetime="{note['date']}">{pretty_date(note['date'])}</time>{tag}</div>
    <h1>{esc(note['title'])}</h1>
    {chr(10).join('    ' + b for b in body).strip()}
  </article>
  <div class="about">
    <p>This is a short, dated note by {esc(AUTHOR)}. Notes are working ideas and arguments, not peer-reviewed results, and each says what would test it. See all <a href="../">research notes</a> or the <a href="../../">home page</a>.</p>
  </div>
  {pager_html}
</main>
""" + footer_html())


def index_page(notes):
    url = SITE + "notes/"
    description = ("Short, dated notes on questions of method and interpretation in plant stress "
                   "research, by Dr. Md. Mezanur Rahman. Working ideas, not peer-reviewed results.")
    if notes:
        entries = []
        for n in notes:
            tag = f'<span class="tag">{esc(n["tag"])}</span>' if n.get("tag") else ""
            entries.append(f"""    <li>
      <div class="meta"><time datetime="{n['date']}">{pretty_date(n['date'])}</time>{tag}</div>
      <h2><a href="{esc(n['id'])}/">{esc(n['title'])}</a></h2>
      <p>{esc(shorten(first_paragraph(n), 230))}</p>
    </li>""")
        listing = '<ol class="list">\n' + "\n".join(entries) + "\n  </ol>"
    else:
        listing = '<p class="lead">No notes have been published yet.</p>'

    return (head(f"Research notes | {AUTHOR}", description, url, "notes.css")
            + header_html("../")
            + f"""<main id="main">
  <p class="crumbs"><a href="../">Home</a> &rsaquo; Research notes</p>
  <h1>Notes on plant stress physiology</h1>
  <p class="lead">Short, dated notes on questions of method and interpretation in plant stress research. They are working ideas and arguments, not peer-reviewed results, and each says what would test it.</p>
  {listing}
</main>
""" + footer_html())


def not_found_page():
    css = SITE + "notes/notes.css"
    return (head(f"Page not found | {AUTHOR}", "This page does not exist.", SITE, css, robots="noindex")
            + header_html(SITE)
            + """<main id="main">
  <h1>Page not found</h1>
  <p class="lead">The address you followed does not exist on this site. Try the <a href=\"""" + SITE + """\">home page</a> or the <a href=\"""" + SITE + """notes/\">research notes</a>.</p>
</main>
""" + footer_html())


# ------------------------------------------------------------------- sitemap

def build_sitemap(notes):
    keep = []
    if SITEMAP.exists():
        text = SITEMAP.read_text(encoding="utf-8")
        for block in re.findall(r"<url>(.*?)</url>", text, re.S):
            loc = re.search(r"<loc>(.*?)</loc>", block)
            mod = re.search(r"<lastmod>(.*?)</lastmod>", block)
            if loc and not loc.group(1).startswith(SITE + "notes/"):
                keep.append((loc.group(1), mod.group(1) if mod else None))
    if not any(u == SITE for u, _ in keep):
        keep.insert(0, (SITE, datetime.now(timezone.utc).strftime("%Y-%m-%d")))

    entries = list(keep)
    if notes:
        entries.append((SITE + "notes/", notes[0]["date"]))
        entries += [(f"{SITE}notes/{n['id']}/", n["date"]) for n in notes]

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, mod in entries:
        lines.append("  <url>")
        lines.append(f"    <loc>{esc(loc)}</loc>")
        if mod:
            lines.append(f"    <lastmod>{mod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def main():
    notes = load_notes()
    if NOTES_DIR.exists():
        shutil.rmtree(NOTES_DIR)
    NOTES_DIR.mkdir(parents=True)

    write(NOTES_DIR / "notes.css", fonts_css() + PAGE_CSS)
    write(NOTES_DIR / "index.html", index_page(notes))
    for i, note in enumerate(notes):
        newer = notes[i - 1] if i > 0 else None
        older = notes[i + 1] if i + 1 < len(notes) else None
        write(NOTES_DIR / note["id"] / "index.html", note_page(note, newer, older))
        print(f"Built notes/{note['id']}/")

    write(NOT_FOUND, not_found_page())
    write(SITEMAP, build_sitemap(notes))
    print(f"Built {len(notes)} note page(s), the notes index, 404.html and sitemap.xml")
    return 0


if __name__ == "__main__":
    sys.exit(main())

