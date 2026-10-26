"""Parser, merge, robots, and schedule checks. No network."""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scraper.commbuys import assign_flags
from scraper.dates import format_due, is_past, parse_when
from scraper.parsers.bidnet import parse_bidnet
from scraper.parsers.civicplus import close_kind, enrich_civicplus_detail, parse_civicplus
from scraper.parsers.documents import parse_document_page
from scraper.parsers.wordpress import enrich_wordpress_post, parse_wordpress_list
from scraper.robots import parse_robots
from scraper.scrape import _finish_civicplus
from scraper.store import canonical_url, merge_bids, new_open_bids

ROOT = Path(__file__).resolve().parents[1]
EASTERN = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 26, 14, 0, tzinfo=EASTERN)

CIVIC = """
<div class="bidItems listItems">
  <div class="bidsHeader listHeader"><span>Construction</span><span>1 Bid</span></div>
  <div class="listItemsRow bid">
    <div class="bidTitle">
      <span><a href="bids.aspx?bidID=48">Maple Street Park</a></span><br>
      <span><strong>Bid No.</strong> ED 2026-11</span>
    </div>
    <div class="bidStatus">
      <div><span>Status:</span><br><span>Closes:</span></div>
      <div><span>Open</span><br><span>10/29/2026 9:00 AM</span></div>
    </div>
  </div>
  <div class="bidsHeader listHeader"><span>Goods</span><span>1 Bid</span></div>
  <div class="listItemsRow bid alt">
    <div class="bidTitle"><span><a href="bids.aspx?bidID=9">Old award</a></span></div>
    <div class="bidStatus">
      <div><span>Status:</span><br><span>Closes:</span></div>
      <div><span>Open</span><br><span>Upon Contract</span></div>
    </div>
  </div>
</div>
"""

BIDNET = """
<table class="mets-table">
  <tr class="mets-table-row odd">
    <td>
      <div class="sol-num">FY2027-001</div>
      <div class="sol-title">
        <a id="g_9" href="/massachusetts/solicitations/open-bids/FY2027-001-Golf/1" class="solicitation-link mets-command-link">FY2027-001 Golf Course Maintenance Services</a>
      </div>
      <span class="sol-publication-date"><span class="date-value">10/26/2026</span></span>
      <span class="sol-closing-date open"><span class="date-value">12/01/2026</span></span>
    </td>
  </tr>
  <tr class="mets-table-row-empty" aria-hidden="true">
    <td><span>There are no open bids at this time.</span></td>
  </tr>
</table>
"""

HOLLISTON = """
<div class="fr-view">
  <h3><a href="/DocumentCenter/View/11239/SOW">SOW - Pinecrest Rock Formation Restoration</a> (Quotes due by October 30, 2026 at 10:00am)</h3>
  <h3><a href="/DocumentCenter/View/11132/HR">RFP for HR Services</a> (Due by October 5, 2026 by 12:00pm)</h3>
  <h3><a href="/DocumentCenter/View/11075/Trails">Holliston Trails Inventory RFP</a> (Bids due by September 30, 2026 by 4pm)
    <a href="/DocumentCenter/View/11106/Addendum-1">Addendum #1</a></h3>
  <h3>View All Facility Closed Bid Sheets <a href="/594/Closed-Bid-Sheets">HERE</a></h3>
</div>
<script></script>
"""

SUDBURY_LIST = """
<table>
  <tr><th>Title</th><th>Date Published</th><th>Department/Committee</th><th>Categories</th></tr>
  <tr>
    <td><a href="https://sudbury.ma.us/facilities/2026/10/21/nixon/">Invitation for Bids - Nixon Fire Alarm</a></td>
    <td>Wednesday, October 21, 2026</td>
    <td>Facilities Department</td>
    <td>Request for Proposal</td>
  </tr>
</table>
"""

SUDBURY_POST = """
<p>The Office invites sealed bids until November 5, 2026 at 12:00pm at which time they will be publicly opened.</p>
<p>Questions will be received until October 29, 2026 at 12:00PM.</p>
<p>Contract No. 2026-CS-1</p>
"""


class DatesTest(unittest.TestCase):
    def test_numeric_and_month_forms(self):
        self.assertEqual(parse_when("11/4/2026 10:00 AM"), "2026-11-04T10:00:00-05:00")
        self.assertEqual(parse_when("10/29/2026 9:00 AM"), "2026-10-29T09:00:00-04:00")
        self.assertEqual(parse_when("October 30, 2026 at 10:00am"), "2026-10-30T10:00:00-04:00")
        self.assertEqual(parse_when("November 12, 2026 at 10am"), "2026-11-12T10:00:00-05:00")
        self.assertEqual(parse_when("12/01/2026"), "2026-12-01")
        self.assertIsNone(parse_when("Upon Contract"))
        self.assertIsNone(parse_when("Open Until Contracted"))

    def test_past_treats_date_only_as_open_all_day(self):
        self.assertFalse(is_past("2026-10-26", NOW))
        self.assertTrue(is_past("2026-10-25", NOW))
        self.assertFalse(is_past("2026-10-26T18:00:00-04:00", NOW))
        self.assertTrue(is_past("2026-10-26T09:00:00-04:00", NOW))

    def test_format_due(self):
        self.assertIn("Nov 4, 2026", format_due("2026-11-04T10:00:00-05:00"))
        self.assertIn("10:00 AM", format_due("2026-11-04T10:00:00-05:00"))


class RobotsTest(unittest.TestCase):
    def test_blank_line_does_not_drop_rules(self):
        policy = parse_robots(
            """
            User-agent: *

            Disallow: /login
            Disallow: /*?*page=
            Crawl-delay: 5

            User-agent: GPTBot
            Disallow: /
            """
        )
        ua = "bid-watch/1.0 (personal municipal bid tracker; +https://github.com/solopass/bid-watch)"
        self.assertTrue(policy.can_fetch(ua, "https://www.bidnetdirect.com/massachusetts/townwestborough"))
        self.assertFalse(policy.can_fetch(ua, "https://www.bidnetdirect.com/login"))
        self.assertFalse(
            policy.can_fetch(ua, "https://www.bidnetdirect.com/massachusetts/townwestborough?page=2")
        )
        self.assertEqual(policy.crawl_delay(ua), 5)
        self.assertFalse(policy.can_fetch("GPTBot", "https://www.bidnetdirect.com/massachusetts/townwestborough"))

    def test_civicplus_star_allows_bids_named_bots_do_not(self):
        text = (ROOT / "tests" / "fixtures" / "civicplus-robots.txt").read_text(encoding="utf-8")
        policy = parse_robots(text)
        ua = "bid-watch/1.0 (personal municipal bid tracker; +https://github.com/solopass/bid-watch)"
        self.assertTrue(policy.can_fetch(ua, "https://www.framinghamma.gov/Bids.aspx"))
        self.assertTrue(policy.can_fetch(ua, "https://www.framinghamma.gov/bids.aspx?bidID=550"))
        self.assertFalse(policy.can_fetch(ua, "https://www.framinghamma.gov/search.aspx"))
        self.assertFalse(policy.can_fetch("GPTBot", "https://www.framinghamma.gov/Bids.aspx"))
        self.assertEqual(policy.crawl_delay(ua), 10)


class ParserTest(unittest.TestCase):
    def test_civicplus_skips_upon_contract_by_default(self):
        items = parse_civicplus(CIVIC, "https://www.example.gov/Bids.aspx")
        self.assertEqual(len(items), 2)
        kept = [
            item
            for item in items
            if _finish_civicplus(item, {"when_contract": "skip"}, fetcher=None, known={})
        ]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["bid_number"], "ED 2026-11")
        self.assertEqual(kept[0]["category"], "Construction")
        self.assertEqual(kept[0]["due"], "2026-10-29T09:00:00-04:00")
        self.assertEqual(close_kind(items[1]), "contract")

    def test_bidnet_ignores_hidden_empty_row(self):
        items = parse_bidnet(BIDNET, "https://www.bidnetdirect.com/massachusetts/townwestborough")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["bid_number"], "FY2027-001")
        self.assertEqual(items[0]["title"], "Golf Course Maintenance Services")
        self.assertEqual(items[0]["posted_date"], "2026-10-26")
        self.assertEqual(items[0]["due"], "2026-12-01")

    def test_holliston_headings(self):
        items = parse_document_page(HOLLISTON, "https://www.townofholliston.us/592/Procurement-Legal-Notices")
        titles = [item["title"] for item in items]
        self.assertEqual(
            titles,
            [
                "SOW - Pinecrest Rock Formation Restoration",
                "RFP for HR Services",
                "Holliston Trails Inventory RFP",
            ],
        )
        self.assertTrue(items[0]["url"].endswith("/DocumentCenter/View/11239/SOW"))
        self.assertEqual(items[0]["due"], "2026-10-30T10:00:00-04:00")
        self.assertTrue(is_past(items[1]["due"], NOW))

    def test_sudbury_picks_bid_opening_not_questions(self):
        items = parse_wordpress_list(SUDBURY_LIST, "https://sudbury.ma.us/bids/")
        self.assertEqual(items[0]["posted_date"], "2026-10-21")
        self.assertEqual(items[0]["category"], "Facilities Department")
        extra = enrich_wordpress_post(SUDBURY_POST)
        self.assertEqual(extra["due"], "2026-11-05T12:00:00-05:00")
        self.assertEqual(extra["bid_number"], "2026-CS-1")

    def test_detail_body_date_when_meta_says_until_contracted(self):
        html = """
        <meta property="og:description" content="Bid Publication Date/Time: 11/19/2025 12:00 AM; Closing Date/Time: Open Until Contracted" />
        <p>Bids must be received by Wednesday, December 10, 2025 at 11:00 AM at Town House.</p>
        """
        extra = enrich_civicplus_detail(html)
        self.assertEqual(extra["posted_date"], "2025-11-19")
        self.assertEqual(extra["due"], "2025-12-10T11:00:00-05:00")


class StoreTest(unittest.TestCase):
    def test_dedup_first_seen_and_close_rules(self):
        source = canonical_url("https://www.example.gov/Bids.aspx")
        fresh = [
            {
                "town": "Framingham",
                "title": "Roof",
                "bid_number": "9255",
                "url": "https://www.example.gov/Bids.aspx?bidID=550",
                "source_url": source,
                "due": "2026-11-04T10:00:00-05:00",
                "category": "Bids",
                "posted_date": None,
            },
            {
                "town": "Framingham",
                "title": "Roof copy",
                "bid_number": "9255",
                "url": "https://www.example.gov/bids.aspx?bidID=550&utm_source=x",
                "source_url": source,
                "due": "2026-11-04T10:00:00-05:00",
                "category": "Bids",
                "posted_date": "2026-10-20",
            },
        ]
        first = merge_bids([], fresh, {source}, NOW)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["first_seen"], "2026-10-26")
        self.assertEqual(first[0]["status"], "open")
        self.assertEqual(first[0]["posted_date"], "2026-10-20")
        self.assertEqual(canonical_url(first[0]["url"]), "https://www.example.gov/bids.aspx?bidID=550")

        second = merge_bids(first, [], {source}, datetime(2026, 10, 27, 12, tzinfo=EASTERN))
        self.assertEqual(second[0]["status"], "closed")
        self.assertEqual(second[0]["first_seen"], "2026-10-26")
        self.assertEqual(second[0]["closed_on"], "2026-10-27")

        kept = merge_bids(first, [], set(), datetime(2026, 10, 27, 12, tzinfo=EASTERN))
        self.assertEqual(kept[0]["status"], "open")

        past = merge_bids(
            [],
            [{
                "town": "Holliston",
                "title": "HR",
                "url": "https://www.townofholliston.us/DocumentCenter/View/1/HR",
                "source_url": "https://www.townofholliston.us/592/Procurement-Legal-Notices",
                "due": "2026-10-05T12:00:00-04:00",
            }],
            {"https://www.townofholliston.us/592/Procurement-Legal-Notices"},
            NOW,
        )
        self.assertEqual(past[0]["status"], "closed")
        self.assertEqual(new_open_bids([], past), [])
        self.assertEqual(len(new_open_bids([], first)), 1)

    def test_commbuys_matches_municipality_not_a_similar_name(self):
        rows = [
            {
                "Bid Solicitation #": "BD-27-1",
                "Organization Name": "Town of Natick",
                "Description": "IFB: Generator Maintenance, Repair, and Emergency Repair",
                "Alternate Id": "",
            },
            {
                "Bid Solicitation #": "BD-27-2",
                "Organization Name": "Framingham State University",
                "Description": "Public Safety Radio",
                "Alternate Id": "",
            },
            {
                "Bid Solicitation #": "BD-27-3",
                "Organization Name": "City of Marlborough",
                "Description": "ED 2026-11 (RE-BID)",
                "Alternate Id": "",
            },
        ]
        bids = [
            {"town": "Natick", "title": "IFB: Generator Maintenance, Repair, Emer. Repair - 3 Years", "bid_number": None},
            {"town": "Framingham", "title": "DPW Office Trailer Roof Replacement", "bid_number": "9255"},
            {"town": "Marlborough", "title": "Maple Street Park (Renovations) - REBID", "bid_number": "ED 2026-11"},
        ]
        assign_flags(bids, rows)
        self.assertEqual(bids[0]["on_commbuys"], "yes")
        self.assertIn("BD-27-1", bids[0]["commbuys_url"])
        self.assertEqual(bids[1]["on_commbuys"], "no")
        self.assertEqual(bids[2]["on_commbuys"], "yes")
        assign_flags(bids, None)
        self.assertEqual(bids[0]["on_commbuys"], "yes")


class ScheduleTest(unittest.TestCase):
    def test_workflow_is_daily_plus_manual_only(self):
        text = (ROOT / ".github" / "workflows" / "scrape.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertIn('cron: "0 11 * * *"', text)
        self.assertNotIn("push:", text)
        self.assertNotIn("pull_request:", text)
        self.assertEqual(text.count("cron:"), 1)
        page = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn(
            "https://github.com/Solopass/bid-watch/actions/workflows/scrape.yml",
            page,
        )
        self.assertNotIn("ghp_", page)
        self.assertNotIn("github_pat_", page)
        self.assertNotIn("token", page.lower())


if __name__ == "__main__":
    unittest.main()
