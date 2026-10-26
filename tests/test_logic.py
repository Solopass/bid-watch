"""Parser, merge, robots, and schedule checks. No network."""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scraper.commbuys import assign_flags, covered_rows
from scraper.legal import apply_notices, parse_notice_page
from scraper.pdftext import text_from_content
from scraper.parsers.bidexpress import parse_bid_express
from scraper.parsers.massdevelopment import parse_massdevelopment
from scraper.parsers.mbta import parse_current_solicitations, parse_future_solicitations
from scraper.parsers.msba import parse_bid_schedule_text
from scraper.places import match_location, match_organization, town_regions_from_config
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

    def test_covered_rows_skip_matches_and_statewide(self):
        regions = town_regions_from_config()
        self.assertEqual(regions["Framingham"], "MetroWest core")
        self.assertEqual(regions["Wellesley"], "Next ring")
        self.assertEqual(len(regions), 27)
        self.assertIsNone(match_organization("Framingham State University", regions))
        self.assertIsNone(match_organization("Massachusetts Department of Transportation", regions))
        self.assertEqual(match_organization("Town of Weston", regions).town, "Weston")
        self.assertEqual(match_organization("Town of Westborough", regions).town, "Westborough")
        self.assertEqual(match_organization("Natick Public Schools", regions).town, "Natick")
        self.assertEqual(match_organization("Natick Housing Authority", regions).town, "Natick")
        self.assertEqual(match_organization("Sudbury Water District", regions).town, "Sudbury")
        assabet = match_organization(
            "Assabet Valley Regional Vocational School District", regions
        )
        self.assertEqual(assabet.town, "Assabet Valley")
        self.assertEqual(assabet.region, "MetroWest core")
        self.assertEqual(match_location("Natick, MA", regions).town, "Natick")
        self.assertIsNone(match_location("Bridge replacement in Natick", regions))

        rows = [
            {
                "Bid Solicitation #": "BD-A",
                "Organization Name": "Town of Westborough",
                "Description": "Golf Course Maintenance Services",
                "Alternate Id": "FY2027-001",
                "Bid Opening Date": "12/01/2026 10:00 AM",
            },
            {
                "Bid Solicitation #": "BD-B",
                "Organization Name": "Town of Westborough",
                "Description": "RFQ General and FSB Contractor for New Dispatch Facility",
                "Alternate Id": "",
                "Bid Opening Date": "11/04/2026 1:00 PM",
            },
            {
                "Bid Solicitation #": "BD-C",
                "Organization Name": "Framingham State University",
                "Description": "Public Safety Radio",
                "Alternate Id": "",
                "Bid Opening Date": "12/01/2026",
            },
            {
                "Bid Solicitation #": "BD-D",
                "Organization Name": "Assabet Valley Regional Vocational School District",
                "Description": "IFB 631 On-call plumbing",
                "Alternate Id": "",
                "Bid Opening Date": "11/10/2026 10:00 AM",
            },
            {
                "Bid Solicitation #": "BD-E",
                "Organization Name": "Massachusetts Department of Transportation",
                "Description": "Bridge replacement NATICK",
                "Alternate Id": "",
                "Bid Opening Date": "12/01/2026",
            },
        ]
        bids = [
            {
                "town": "Westborough",
                "source": "town",
                "title": "Golf Course Maintenance Services",
                "bid_number": "FY2027-001",
            }
        ]
        extra = covered_rows(bids, rows, regions)
        assign_flags(bids, rows, regions)
        self.assertEqual(bids[0]["on_commbuys"], "yes")
        self.assertEqual(sorted(item["town"] for item in extra), ["Assabet Valley", "Westborough"])
        self.assertEqual({item["source"] for item in extra}, {"commbuys"})
        self.assertTrue(all(item["on_commbuys"] == "yes" for item in extra))
        dispatch = next(item for item in extra if item["town"] == "Westborough")
        self.assertIn("Dispatch", dispatch["title"])
        self.assertEqual(dispatch["bid_number"], "BD-B")
        self.assertNotIn("BD-A", [item["bid_number"] for item in extra])


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
        self.assertIn('id="source"', page)
        self.assertIn("MetroWest core", page)
        self.assertIn("Next ring", page)
        self.assertIn("central_register", page)
        self.assertIn("Legal notices", page)
        self.assertIn("Bid Express", page)
        self.assertIn("MassDevelopment", page)
        self.assertIn("MBTA", page)
        self.assertIn("MSBA", page)
        self.assertIn("Statewide agency", page)
        self.assertIn("Upcoming projects", page)
        self.assertNotIn("ghp_", page)
        self.assertNotIn("github_pat_", page)
        self.assertNotIn("token", page.lower())
        config = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
        next_ring = [item["town"] for item in config["sources"] if item["region"] == "Next ring"]
        self.assertEqual(
            next_ring,
            [
                "Wellesley", "Weston", "Needham", "Shrewsbury", "Stow", "Acton",
                "Concord", "Dover", "Sherborn", "Bellingham", "Upton", "Grafton",
                "Maynard", "Lincoln", "Mendon",
            ],
        )
        self.assertTrue(all(item["type"] == "civicplus" for item in config["sources"] if item["region"] == "Next ring"))
        feed_types = {item["type"] for item in config["feeds"]}
        self.assertEqual(
            feed_types,
            {"legal_notice", "bid_express", "massdevelopment", "mbta_current", "mbta_future", "msba"},
        )


class StatewideSourceTest(unittest.TestCase):
    def test_bid_express_reads_the_upcoming_table_only(self):
        html = """
        <h3>Upcoming Solicitations</h3>
        <table id="solicitations"><tr id="Solicitation_48902">
          <td><a href="/solicitations/48902">DCP2604 Sub Bids</a>
          <div data-popup-content="Chelsea office renovation">Chelsea office renovation</div></td>
          <td id="Solicitation_48902_Deadline">10/27/2026 04:00 PM UTC</td>
        </tr></table>
        <h3><span>Closed Solicitations</span></h3>
        <table><tr id="Solicitation_1">
          <td><a href="/solicitations/1">OLD</a><div data-popup-content="Closed job">Closed job</div></td>
          <td id="Solicitation_1_Deadline">01/02/2020 04:00 PM UTC</td>
        </tr></table>
        """
        rows = parse_bid_express(html, "https://www.bidexpress.com/businesses/10279/home", "DCAMM")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["town"], "DCAMM")
        self.assertEqual(rows[0]["region"], "Statewide agency")
        self.assertEqual(rows[0]["source"], "bid_express")
        self.assertEqual(rows[0]["kind"], "open")
        self.assertEqual(rows[0]["due"], "2026-10-27T12:00:00-04:00")
        self.assertEqual(rows[0]["url"], "https://www.bidexpress.com/solicitations/48902")

    def test_massdevelopment_pairs_heading_with_the_next_pdf(self):
        html = """
        <h5>NOTICE TO BID: Gas path, Devens, MA MassDevelopment Project No. 26-021</h5>
        <a href="https://www.massdevelopment.com/assets/document/RFP/Legal_Notice_26-021_FINAL.pdf">More information</a>
        <h5>Capital providers for PACE</h5>
        <p>Responses are due January 15, 2027.</p>
        <a href="/assets/pace.pdf">More information</a>
        """
        rows = parse_massdevelopment(html, "https://www.massdevelopment.com/rfps-and-rfqs/")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["bid_number"], "26-021")
        self.assertEqual(rows[0]["region"], "Statewide agency")
        self.assertTrue(rows[0]["url"].endswith("Legal_Notice_26-021_FINAL.pdf"))
        self.assertEqual(rows[1]["due"], "2027-01-15")
        self.assertEqual(rows[1]["url"], "https://www.massdevelopment.com/assets/pace.pdf")

    def test_mbta_future_rows_are_upcoming_and_statewide(self):
        html = """
        <table>
          <tr><th>Title</th><th>Contract Number</th><th>Adv. Date</th><th>Bid Date</th><th>Type of work</th><th>Engineer's Estimate</th></tr>
          <tr><td>Haverhill Layover Building Demolition</td><td>F64CN01</td><td>11/04/2026</td><td>12/03/2026</td><td>Demolition</td><td>$560,000</td></tr>
          <tr><td>Escalator Replacement, Boston, MA</td><td>TBD</td><td>11/11/2026</td><td>12/10/2026</td><td>Station Improvements</td><td>$8,000,000</td></tr>
        </table>
        """
        page = "https://bc.mbta.com/business_center/bidding_solicitations/future_solicitations/"
        rows = parse_future_solicitations(html, page)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["kind"], "upcoming")
        self.assertEqual(rows[0]["region"], "Statewide agency")
        self.assertEqual(rows[0]["town"], "MBTA")
        self.assertEqual(rows[0]["bid_number"], "F64CN01")
        self.assertEqual(rows[0]["due"], "2026-12-03")
        self.assertIsNone(rows[1]["bid_number"])
        self.assertNotEqual(canonical_url(rows[0]["url"]), canonical_url(rows[1]["url"]))
        current = """
        <h4>Current Construction Contract Bid Solicitations</h4>
        <ul><!--contract_nbr: F79CN01--><li><a href="/business_center/bidding_solicitations/design_and_construction/construction_bid/?cbid=7507">South Station Crew Base, Boston, MA</a></li></ul>
        <h4>Awarded MBTA Contracts</h4>
        <ul><li><a href="/awarded/">View awarded</a></li></ul>
        """
        live = parse_current_solicitations(current, "https://bc.mbta.com/business_center/bidding_solicitations/current_solicitations/")
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["kind"], "open")
        self.assertEqual(live[0]["bid_number"], "F79CN01")
        self.assertEqual(live[0]["region"], "Statewide agency")

    def test_msba_keeps_covered_towns_and_not_a_lookalike(self):
        text = "\n".join([
            "MarlboroughES950New$ 162.5Mon 8/2/27Fri 10/1/27$ 129.3GMP targeted: October 2027",
            "North AndoverES335New$ 79.8Wed 7/7/27Fri 9/10/27$ 63.2NTP targeted: September 2027",
            "AndoverES100New$ 10.0Mon 1/4/27Tue 2/2/27$ 8.0NTP targeted: February 2027",
            "NeedhamMS1335Add/Reno$ 0.0Mon 2/28/28Tue 4/25/28$ 0.0GMP targeted: April 2028",
        ])
        regions = {"Marlborough": "MetroWest core", "Needham": "Next ring", "Dover": "Next ring"}
        rows = parse_bid_schedule_text(
            text,
            "https://www.massschoolbuildings.org/schedule.pdf",
            "https://www.massschoolbuildings.org/building/CP_Information_Project_Info",
            regions,
        )
        self.assertEqual([row["town"] for row in rows], ["Marlborough", "Needham"])
        self.assertEqual(rows[0]["kind"], "upcoming")
        self.assertEqual(rows[0]["region"], "MetroWest core")
        self.assertEqual(rows[0]["due"], "2027-08-02")
        self.assertEqual(rows[1]["region"], "Next ring")
        self.assertIn("#", rows[0]["url"])

    def test_pdf_text_keeps_words_and_breaks_on_a_wide_gap(self):
        content = b"[(Marl)5(borough)]TJ\n-800 (ES) Tj"
        text = text_from_content(content)
        self.assertIn("Marlborough", text.replace(" ", ""))
        self.assertIn("\n", text)

    def test_legal_notice_flags_a_town_bid_and_drops_other_towns(self):
        html = """
        <table class="nested">
          <tr><td><input type="hidden" value="1" />
            <a onclick="Details.aspx?SID=abc&amp;ID=923821">view</a></td>
            <td><div class="left"><strong>MetroWest Daily News, The</strong><br>Wednesday, October 21, 2026</div>
            <div class="right">City: Marlborough</div></td></tr>
          <tr><td colspan="3">MPS 2027-01 LEGAL NOTICE INVITATION TO BID CITY OF MARLBOROUGH HIGH SCHOOL FIELD HOUSE FLOORING</td></tr>
        </table>
        <table class="nested">
          <tr><td><a onclick="Details.aspx?ID=2">view</a></td>
            <td><strong>Melrose Weekly News</strong><br>Friday, October 23, 2026</td></tr>
          <tr><td colspan="3">CITY OF MELROSE INVITATION TO BID sealed bids irrigation</td></tr>
        </table>
        """
        regions = {"Marlborough": "MetroWest core", "Melrose": "Next ring"}
        # Melrose is not covered. Only Marlborough is in the real map; pass both and then
        # confirm a town outside the map is ignored by using the real filter.
        notices = parse_notice_page(html, {"Marlborough": "MetroWest core"})
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["town"], "Marlborough")
        self.assertEqual(notices[0]["source"], "legal_notice")
        self.assertEqual(notices[0]["paper"], "MetroWest Daily News, The")
        self.assertTrue(notices[0]["url"].endswith("Details.aspx?ID=923821"))
        town_bid = {
            "town": "Marlborough",
            "source": "town",
            "title": "High School Field House Flooring",
            "bid_number": "MPS 2027-01",
            "url": "https://www.marlborough-ma.gov/bids.aspx?bidID=1",
        }
        other = {
            "town": "Hudson",
            "source": "town",
            "title": "High School Field House Flooring",
            "bid_number": "MPS 2027-01",
            "url": "https://www.townofhudson.org/bids.aspx?bidID=9",
        }
        unmatched = apply_notices([town_bid, other], notices)
        self.assertEqual(unmatched, [])
        self.assertEqual(town_bid["legal_notice_url"], notices[0]["url"])
        self.assertIsNone(other["legal_notice_url"])
        self.assertEqual(regions["Marlborough"], "MetroWest core")

    def test_kind_defaults_to_open_and_fragments_stay_unique(self):
        source = canonical_url("https://www.example.gov/Bids.aspx")
        merged = merge_bids(
            [],
            [{
                "town": "Framingham",
                "title": "Roof",
                "url": "https://www.example.gov/Bids.aspx?bidID=1",
                "source_url": source,
                "due": "2026-12-01",
            }],
            {source},
            NOW,
        )
        self.assertEqual(merged[0]["kind"], "open")
        self.assertIsNone(merged[0]["legal_notice_url"])
        upcoming = "https://bc.mbta.com/future/#haverhill"
        other = "https://bc.mbta.com/future/#quincy"
        self.assertNotEqual(canonical_url(upcoming), canonical_url(other))
        self.assertIn("haverhill", canonical_url(upcoming))


if __name__ == "__main__":
    unittest.main()
