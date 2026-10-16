#!/usr/bin/env python3
"""Re-check the hand-picked graduate-opportunity entries and stamp the date.

What it does (once a night, just after midnight New York time):
  1. Reads the hand-picked entries ("opp-seed" block) in index.html.
  2. Opens every entry link and programme link.
  3. Classifies each link:
       ok       - the page opened (HTTP 2xx or 3xx)
       blocked  - the site refused or timed out (403, 429, 5xx, timeout).
                  This is NOT counted as a failure, but also NOT counted as verified.
       dead     - the page is gone (404, 410) or the address does not exist.
  4. If no link is dead, it sets "checked_on" to today's New York date and sets
     "checked" on every entry that opened.  If any link is dead, the date is left
     alone, so the page never claims a check that did not succeed.
  5. On its first run it changes the wording on the page from
     "Hand-checked entries last verified" to
     "Hand-picked entries re-checked automatically on", because the date is now
     set by this script and not by a person.

Nothing is changed if every request fails (for example, no internet).
"""
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

INDEX = "index.html"
NY = ZoneInfo("America/New_York")
OLD_LABEL = "Hand-checked entries last verified "
NEW_LABEL = "Hand-picked entries re-checked automatically on "
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 (link check for mezanurrahman.com)")
SEED_RE = re.compile(r'(<script type="application/json" id="opp-seed">)(.*?)(</script>)', re.S)


PLAIN_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36")
DETAIL = {}   # url -> text explaining the result, used in the log

# Pages that open normally in a browser but answer this checker with "404".
# Only add a page here after you have opened it yourself and seen that it loads.
# It is then treated as "unverified" instead of "dead".
OPENS_IN_BROWSER = [
]

# Entries to take off the page. Add an address here when you have confirmed that the
# posting is gone. The script removes the entry from the hand-picked list on its next
# run and says so in the log.
REMOVE_URLS = [
    "https://www.agronomy.k-state.edu/about/employment/graduate-assistantships.html",
    "https://plscgradprogram.utk.edu/common-questions-about-graduate-admission-to-plant-sciences/",
]


def _open(url, ua, timeout):
    req = urllib.request.Request(url, headers={
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read(2048)
        return resp.status


def fetch_status(url, timeout=25):
    """Return 'ok', 'blocked' or 'dead' for one address.

    A page counts as dead only if it is missing (404/410) or the address does not
    exist, and the answer is the same with two different browser identities. Many
    sites answer automated visitors differently, so one 404 is not trusted on its own.
    """
    url = url.split("#", 1)[0]
    outcomes = []
    for ua in (UA, PLAIN_UA):
        try:
            code = _open(url, ua, timeout)
            DETAIL[url] = "HTTP %s" % code
            return "ok" if code < 400 else "blocked"
        except urllib.error.HTTPError as err:
            outcomes.append("HTTP %s" % err.code)
            if err.code not in (404, 410):
                DETAIL[url] = "HTTP %s" % err.code
                return "blocked"
        except urllib.error.URLError as err:
            reason = getattr(err, "reason", None)
            if isinstance(reason, socket.gaierror):
                outcomes.append("address not found")
            else:
                DETAIL[url] = "connection problem: %s" % reason
                return "blocked"
        except (socket.timeout, TimeoutError, ConnectionError, OSError) as err:
            DETAIL[url] = "timeout or connection problem"
            return "blocked"
        time.sleep(3)
    if url in OPENS_IN_BROWSER:
        DETAIL[url] = "%s for this checker, but the page opens in a normal browser" % " / ".join(outcomes)
        return "blocked"
    DETAIL[url] = " / ".join(outcomes)
    return "dead"


def main():
    force = "--force" in sys.argv or os.environ.get("FORCE_CHECK") == "1"
    now_ny = datetime.now(NY)
    if not force and now_ny.hour != 0:
        print("It is %s in New York, not the 12 AM hour. Nothing to do." % now_ny.strftime("%H:%M"))
        return 0
    today = now_ny.strftime("%Y-%m-%d")

    html = open(INDEX, encoding="utf-8").read()
    match = SEED_RE.search(html)
    if not match:
        print("Could not find the opp-seed block. Nothing changed.")
        return 0
    seed = json.loads(match.group(2))

    removed = [e for e in seed.get("items", []) if e.get("url") in REMOVE_URLS] + \
              [p for p in seed.get("programs", []) if p.get("url") in REMOVE_URLS]
    for r in removed:
        print("Removed on your instruction (REMOVE_URLS): %s" % (r.get("title") or r.get("name")))
    seed["items"] = [e for e in seed.get("items", []) if e.get("url") not in REMOVE_URLS]
    seed["programs"] = [p for p in seed.get("programs", []) if p.get("url") not in REMOVE_URLS]

    targets = [("entry", e) for e in seed.get("items", [])] + \
              [("programme", p) for p in seed.get("programs", [])]
    results = {}
    for kind, obj in targets:
        url = obj.get("url")
        if not url:
            continue
        results[url] = results.get(url) or fetch_status(url)

    counts = {k: sum(1 for v in results.values() if v == k) for k in ("ok", "blocked", "dead")}
    print("Links checked: %d ok, %d blocked (unverified), %d dead" %
          (counts["ok"], counts["blocked"], counts["dead"]))

    if counts["ok"] == 0:
        print("No link could be reached at all (probably a network problem). Nothing changed.")
        return 0

    for kind, obj in targets:
        url = obj.get("url")
        status = results.get(url)
        if status == "dead":
            print("::warning::Dead link (%s), please review or remove: %s (%s)" % (DETAIL.get(url, "?"), url, obj.get("title") or obj.get("name")))
        elif status == "blocked":
            print("Unverified (%s): %s" % (DETAIL.get(url, "site refused or timed out"), url))
        elif status == "ok" and kind == "entry":
            obj["checked"] = today
        # expired deadlines are reported but never deleted here
        dl = obj.get("deadline")
        if kind == "entry" and obj.get("deadline_kind") == "deadline" and dl and dl < today:
            print("::notice::Deadline has passed (%s): %s" % (dl, obj.get("title")))

    if counts["dead"] == 0:
        seed["checked_on"] = today
        print("No dead links. checked_on set to %s." % today)
    else:
        print("Dead links found, so checked_on was NOT changed (still %s)." % seed.get("checked_on"))

    new_json = json.dumps(seed, indent=1, ensure_ascii=False).replace("</", "<\\/")
    new_html = html[:match.start(2)] + "\n" + new_json + "\n" + html[match.end(2):]
    new_html = new_html.replace(OLD_LABEL, NEW_LABEL)

    if new_html != html:
        with open(INDEX, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_html)
        print("index.html updated.")
    else:
        print("index.html already up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
