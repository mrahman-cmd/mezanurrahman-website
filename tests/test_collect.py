"""Offline tests for scripts/collect.py. They never touch the network."""

import datetime as dt
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import collect  # noqa: E402

TODAY = dt.date(2026, 10, 15)
SOURCE = {"key": "wssa", "name": "WSSA job board", "type": "wp-json", "url": "https://example.org/x"}

GOOD_BODY = (
    "<p>The Department of Plant Pathology at Oklahoma State University in Stillwater, "
    "Oklahoma seeks a PhD student for a graduate research assistantship studying "
    "drought and heat stress tolerance in wheat using phenotyping and genomics.</p>"
    "<p>Start: Fall 2027. Review of applications begins December 1, 2026 and "
    "continues until the position is filled. Application deadline: January 15, 2027.</p>"
)


def raw(title="PhD assistantship: wheat stress tolerance", url="https://example.org/a/", body=GOOD_BODY, posted="2026-10-01"):
    return {"title": title, "url": url, "posted": posted, "body_html": body}


class ExtractionTests(unittest.TestCase):
    def test_html_to_text_strips_tags_and_scripts(self):
        text = collect.html_to_text("<p>One&nbsp;two</p><script>bad()</script><p>three</p>")
        self.assertEqual(text, "One two three")

    def test_dates_in_three_formats(self):
        text = "Due January 15, 2027 or 3 March 2027 or 4/9/2027."
        dates = [d.isoformat() for _, _, d in collect.find_dates(text)]
        self.assertEqual(dates, ["2027-01-15", "2027-03-03", "2027-04-09"])

    def test_invalid_date_is_ignored(self):
        self.assertEqual(collect.find_dates("February 30, 2027"), [])

    def test_deadline_beats_review_date(self):
        text = "Review of applications begins December 1, 2026. Application deadline: January 15, 2027."
        self.assertEqual(collect.extract_deadline(text), ("2027-01-15", "deadline"))

    def test_review_date_only(self):
        text = "Review of applications begins December 1, 2026 and continues until filled."
        self.assertEqual(collect.extract_deadline(text), ("2026-12-01", "review"))

    def test_no_deadline(self):
        self.assertEqual(collect.extract_deadline("Apply soon, no date given."), (None, None))

    def test_degree(self):
        self.assertEqual(collect.extract_degree("PhD position", ""), "PhD")
        self.assertEqual(collect.extract_degree("MS assistantship", ""), "MS")
        self.assertEqual(collect.extract_degree("Graduate positions", "MS or PhD students"), "PhD / MS")
        self.assertEqual(collect.extract_degree("Postdoctoral fellow", ""), "")

    def test_degree_comes_from_title_not_entry_requirements(self):
        body = "Applicants need a BS or MS in agricultural sciences."
        self.assertEqual(collect.extract_degree("PhD assistantship: weed ecology", body), "PhD")
        self.assertEqual(collect.extract_degree("Graduate assistantship", "PhD students sought"), "PhD")

    def test_ms_is_not_lc_ms_or_mississippi(self):
        self.assertEqual(collect.extract_degree("", "LC-MS metabolomics in Starkville, MS"), "")
        self.assertEqual(collect.extract_degree("MS: plant pathology", ""), "MS")

    def test_state_prefers_longer_name(self):
        self.assertEqual(collect.extract_state("", "West Virginia University seeks a student"), "WV")
        self.assertEqual(collect.extract_state("", "Ames, IA 50011"), "IA")
        self.assertEqual(collect.extract_state("", "No place is given here."), "")

    def test_university(self):
        self.assertEqual(collect.extract_university("", "at Oklahoma State University in Stillwater"), "Oklahoma State University")
        self.assertEqual(collect.extract_university("", "The University of Georgia seeks"), "University of Georgia")
        self.assertEqual(collect.extract_university("", "nothing here"), "")

    def test_start_term_and_topics(self):
        self.assertEqual(collect.extract_start_term("Start Fall 2027."), "Fall 2027")
        topics = collect.extract_topics("Weed science PhD", "herbicide resistance and soil health")
        self.assertIn("Weed science", topics)
        self.assertLessEqual(len(topics), 4)

    def test_snippet_is_short(self):
        snippet = collect.make_snippet("word " * 200, "Title")
        self.assertLessEqual(len(snippet), collect.SNIPPET_CHARS + 2)


class RelevanceTests(unittest.TestCase):
    def test_accepts_graduate_plant_positions(self):
        self.assertTrue(collect.is_relevant("PhD assistantship: wheat stress", GOOD_BODY))

    def test_rejects_faculty_staff_and_postdocs(self):
        for title in (
            "Assistant Professor/Extension Sugarbeet Agronomist",
            "Department Head, Plant Sciences",
            "Administrative Assistant",
            "Postdoctoral Researcher in plant genomics",
        ):
            self.assertFalse(collect.is_relevant(title, GOOD_BODY), title)

    def test_rejects_non_plant_graduate_positions(self):
        self.assertFalse(collect.is_relevant("PhD position in astrophysics", "Telescopes and galaxies only."))

    def test_body_only_graduate_signal(self):
        self.assertTrue(collect.is_relevant("Soil microbiology opportunity", "We are recruiting a graduate student to study soil microbes and crop roots."))


class ItemTests(unittest.TestCase):
    def test_build_item_has_page_fields(self):
        item = collect.build_item(raw(), SOURCE, TODAY)
        self.assertIsNotNone(item)
        self.assertEqual(item["state"], "OK")
        self.assertEqual(item["degree"], "PhD")
        self.assertEqual(item["deadline"], "2027-01-15")
        self.assertEqual(item["deadline_kind"], "deadline")
        self.assertTrue(item["rolling"])
        self.assertEqual(item["start_term"], "Fall 2027")
        for key in ("id", "title", "url", "source", "university", "topics", "posted",
                    "snippet", "first_seen", "checked", "rolling"):
            self.assertIn(key, item)

    def test_bad_url_or_title_is_dropped(self):
        self.assertIsNone(collect.build_item(raw(url="javascript:alert(1)"), SOURCE, TODAY))
        self.assertIsNone(collect.build_item(raw(title=""), SOURCE, TODAY))

    def test_expiry_rules(self):
        expired = {"deadline": "2026-10-01", "deadline_kind": "deadline"}
        within_grace = {"deadline": "2026-10-13", "deadline_kind": "deadline"}
        review_only = {"deadline": "2026-01-01", "deadline_kind": "review", "posted": "2026-09-01"}
        old_undated = {"deadline": "", "posted": "2025-01-01"}
        self.assertTrue(collect.is_expired(expired, TODAY))
        self.assertFalse(collect.is_expired(within_grace, TODAY))
        self.assertFalse(collect.is_expired(review_only, TODAY))
        self.assertTrue(collect.is_expired(old_undated, TODAY))


class FeedParsingTests(unittest.TestCase):
    def test_wp_json(self):
        payload = [{
            "date": "2026-09-01T10:00:00",
            "link": "https://wssa.net/2026/09/graduate-research-assistant-ms-2/",
            "title": {"rendered": "Graduate Research Assistant (MS)"},
            "content": {"rendered": "<p>body</p>"},
        }]
        records = collect.parse_wp_json(payload)
        self.assertEqual(records[0]["posted"], "2026-09-01")
        self.assertEqual(records[0]["title"], "Graduate Research Assistant (MS)")

    def test_rss(self):
        xml = """<?xml version="1.0"?>
        <rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
          <channel><item>
            <title>PhD student, plant genomics</title>
            <link>https://www.aspt.net/news-blog/2026/phd</link>
            <pubDate>Fri, 09 Oct 2026 04:09:13 +0000</pubDate>
            <description>short</description>
            <content:encoded><![CDATA[<p>Full text</p>]]></content:encoded>
          </item></channel></rss>"""
        records = collect.parse_rss(xml)
        self.assertEqual(records[0]["posted"], "2026-10-09")
        self.assertEqual(records[0]["body_html"], "<p>Full text</p>")

    def test_rss_with_entity_bomb_is_rejected(self):
        bomb = (
            '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">'
            '<!ENTITY b "&a;&a;&a;&a;">]><rss><channel><item><title>&b;</title></item></channel></rss>'
        )
        with self.assertRaises(Exception):
            collect.parse_rss(bomb)


class PipelineTests(unittest.TestCase):
    def test_collect_records_failures_without_stopping(self):
        sources = [SOURCE, {**SOURCE, "key": "aspt", "name": "ASPT"}]

        def fetch(source):
            if source["key"] == "aspt":
                raise collect.SourceError("HTTP 500")
            return [raw()]

        items, health, ok = collect.collect(sources, fetch, TODAY)
        self.assertEqual(len(items), 1)
        self.assertEqual(ok, {"wssa"})
        self.assertEqual([h["status"] for h in health], ["ok", "error"])

    def test_blocked_status(self):
        def fetch(_):
            raise collect.SourceError("blocked: robots.txt does not allow this request")

        _, health, _ = collect.collect([SOURCE], fetch, TODAY)
        self.assertEqual(health[0]["status"], "blocked")

    def test_merge_keeps_first_seen_and_skips_seed(self):
        old = collect.build_item(raw(), SOURCE, dt.date(2026, 9, 1))
        old["first_seen"] = "2026-09-01"
        fresh = [collect.build_item(raw(), SOURCE, TODAY),
                 collect.build_item(raw(url="https://example.org/seed/"), SOURCE, TODAY)]
        merged = collect.merge([old], fresh, {"wssa"}, {collect.normalise_url("https://example.org/seed")}, TODAY)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["first_seen"], "2026-09-01")

    def test_merge_drops_closed_postings_from_working_source(self):
        old = collect.build_item(raw(), SOURCE, TODAY)
        self.assertEqual(collect.merge([old], [], {"wssa"}, set(), TODAY), [])

    def test_merge_keeps_old_entries_when_source_failed(self):
        old = collect.build_item(raw(), SOURCE, TODAY)
        merged = collect.merge([old], [], set(), set(), TODAY)
        self.assertEqual(len(merged), 1)

    def test_merge_removes_expired(self):
        item = collect.build_item(raw(), SOURCE, TODAY)
        item["deadline"], item["deadline_kind"] = "2026-01-01", "deadline"
        self.assertEqual(collect.merge([], [item], {"wssa"}, set(), TODAY), [])

    def test_output_is_json_and_page_usable(self):
        item = collect.build_item(raw(), SOURCE, TODAY)
        data = json.loads(json.dumps({"generated_at": "2026-10-15T09:23:00Z", "items": [item], "programs": []}))
        entry = data["items"][0]
        self.assertTrue(entry["title"])
        self.assertTrue(collect.is_http_url(entry["url"]))
        self.assertIn(entry["deadline_kind"], ("deadline", "review", ""))

    def test_read_seed_urls_from_page(self):
        import tempfile

        page = ('<script type="application/json" id="opp-seed">'
                '{"items":[{"url":"https://a.org/x/"}],"programs":[{"url":"https://b.org/y"}]}</script>')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "index.html"
            path.write_text(page, encoding="utf-8")
            urls = collect.read_seed_urls(path)
        self.assertEqual(urls, {"https://a.org/x", "https://b.org/y"})
        self.assertEqual(collect.read_seed_urls(Path("/nonexistent/index.html")), set())


if __name__ == "__main__":
    unittest.main()

