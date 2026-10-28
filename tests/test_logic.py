"""Parser, merge, robots, and schedule checks. No network."""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scraper.commbuys import assign_flags, covered_rows
from scraper.invites import to_dashboard_bids
from scraper.legal import apply_notices, parse_notice_page, _primary_town
from scraper.pdftext import text_from_content
from scraper.parsers.bidexpress import parse_bid_express
from scraper.parsers.massdevelopment import parse_massdevelopment
from scraper.parsers.mbta import parse_current_solicitations, parse_future_solicitations
from scraper.parsers.msba import parse_bid_schedule_text
from scraper.parsers.agencies import mapc_in_range, names_outside_place, parse_dcr, parse_mapc, parse_massdot
from scraper.parsers.agendas import agenda_links, lead_lines
from scraper.parsers.cities import parse_boston_bids, parse_boston_projects
from scraper.samgov import filter_opportunities, search_opportunities
from scraper.places import match_location, match_organization, town_regions_from_config
from scraper.trades import cladding_trade
from scraper.dates import format_due, is_past, parse_when
from scraper.parsers.bidnet import parse_bidnet
from scraper.parsers.civicplus import close_kind, enrich_civicplus_detail, parse_civicplus
from scraper.parsers.documents import parse_document_page
from scraper.parsers.wordpress import enrich_wordpress_post, parse_wordpress_list
from scraper.robots import parse_robots
from scraper.scrape import _finish_civicplus, _label_bids
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
        self.assertEqual(regions["Medway"], "Next ring")
        self.assertEqual(regions["Franklin"], "Outer ring")
        self.assertEqual(regions["Hudson"], "MetroWest core")
        self.assertEqual(regions["Hudson NH"], "NH border")
        self.assertEqual(regions["Lincoln"], "Next ring")
        self.assertEqual(regions["Lincoln RI"], "RI border")
        self.assertGreater(len(regions), 27)
        framingham_state = match_organization("Framingham State University", regions)
        self.assertEqual(framingham_state.town, "Framingham State")
        self.assertEqual(framingham_state.sector, "college")
        self.assertEqual(framingham_state.region, "MetroWest core")
        self.assertNotEqual(framingham_state.town, "Framingham")
        massbay = match_organization("Massachusetts Bay Community College", regions)
        self.assertEqual(massbay.town, "MassBay")
        self.assertEqual(massbay.sector, "college")
        worcester_state = match_organization("Worcester State University", regions)
        self.assertEqual(worcester_state.town, "Worcester State")
        self.assertEqual(worcester_state.region, "Outer ring")
        self.assertEqual(
            match_organization("Massachusetts Water Resources Authority", regions).town,
            "MWRA",
        )
        self.assertEqual(
            match_organization("Massachusetts Water Resources Authority", regions).region,
            "Statewide agency",
        )
        self.assertEqual(match_organization("MetroWest Regional Transit Authority", regions).town, "MWRTA")
        self.assertIsNone(match_organization("Massachusetts Department of Transportation", regions))
        self.assertIsNone(match_organization("University of Massachusetts Amherst", regions))
        self.assertEqual(match_organization("Town of Weston", regions).town, "Weston")
        self.assertEqual(match_organization("Town of Westborough", regions).town, "Westborough")
        self.assertEqual(match_organization("Natick Public Schools", regions).town, "Natick")
        self.assertEqual(match_organization("Natick Housing Authority", regions).town, "Natick")
        self.assertEqual(match_organization("Natick Housing Authority", regions).sector, "housing")
        self.assertEqual(match_organization("Natick Public Schools", regions).sector, "school")
        self.assertEqual(match_organization("Town of Dover", regions).town, "Dover")
        dover = match_organization("Dover-Sherborn Regional School District", regions)
        self.assertEqual(dover.town, "Dover-Sherborn")
        self.assertEqual(dover.sector, "school")
        self.assertEqual(dover.region, "Next ring")
        acton = match_organization("Acton-Boxborough Regional School District", regions)
        self.assertEqual(acton.town, "Acton-Boxborough")
        self.assertEqual(match_organization("Town of Acton", regions).town, "Acton")
        mendon = match_organization("Mendon-Upton Regional School District", regions)
        self.assertEqual(mendon.town, "Mendon-Upton")
        self.assertEqual(mendon.region, "Next ring")
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
        self.assertEqual(
            sorted(item["town"] for item in extra),
            ["Assabet Valley", "Framingham State", "Westborough"],
        )
        college = next(item for item in extra if item["town"] == "Framingham State")
        self.assertEqual(college["sector"], "college")
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
        self.assertIn("Early leads", page)
        self.assertIn("Cities", page)
        self.assertIn(">Federal<", page)
        self.assertIn("SAM.gov", page)
        self.assertNotIn("ghp_", page)
        self.assertNotIn("github_pat_", page)
        self.assertNotIn("token", page.lower())
        config = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
        next_ring = [
            item["town"]
            for item in config["sources"]
            if item["region"] == "Next ring" and item.get("sector", "town") == "town"
        ]
        self.assertEqual(
            next_ring,
            [
                "Wellesley", "Weston", "Needham", "Shrewsbury", "Stow", "Acton",
                "Concord", "Dover", "Sherborn", "Bellingham", "Upton", "Grafton",
                "Maynard", "Lincoln", "Mendon", "Medway",
            ],
        )
        self.assertTrue(
            all(
                item["type"] == "civicplus"
                for item in config["sources"]
                if item["region"] == "Next ring"
                and item.get("sector", "town") == "town"
                and item["town"] != "Medway"
            )
        )
        self.assertIn('id="state"', page)
        self.assertIn('id="sector"', page)
        self.assertIn('id="trade"', page)
        self.assertIn("plan_room", page)
        self.assertIn("Plan room invite", page)
        self.assertIn("invites.local.json", page)
        self.assertIn("data/invites.local.json", (ROOT / ".gitignore").read_text(encoding="utf-8"))
        self.assertNotIn("plan_room", (ROOT / "data" / "bids.json").read_text(encoding="utf-8"))
        self.assertIn("Cladding only", page)
        self.assertIn("badge-cladding", page)
        self.assertIn(">College<", page)
        self.assertIn(">Hospital<", page)
        self.assertIn(">Private<", page)
        self.assertIn("Outer ring", page)
        self.assertIn("RI border", page)
        self.assertIn("NH border", page)
        self.assertIn("CT border", page)
        feed_types = {item["type"] for item in config["feeds"]}
        self.assertEqual(
            feed_types,
            {
                "legal_notice",
                "bid_express",
                "massdevelopment",
                "mbta_current",
                "mbta_future",
                "msba",
                "massdot",
                "dcr",
                "mapc",
                "boston_planning",
                "sam_gov",
            },
        )
        workflow = (ROOT / ".github" / "workflows" / "scrape.yml").read_text(encoding="utf-8")
        self.assertIn("SAM_GOV_API_KEY", workflow)


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

    def test_a_named_other_city_does_not_match_a_river(self):
        regions = {"Nashua": "NH border", "Franklin": "Outer ring", "Marlborough": "MetroWest core"}
        self.assertIsNone(
            _primary_town(
                "CITY OF LEOMINSTER INVITATION TO BID North Nashua River Stabilization",
                regions,
            )
        )
        self.assertEqual(
            _primary_town("Town of Franklin invitation to bid crack sealing", regions),
            "Franklin",
        )
        self.assertEqual(
            _primary_town("CITY OF MARLBOROUGH sealed bids near the Nashua River", regions),
            "Marlborough",
        )

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
        self.assertEqual(merged[0]["sector"], "town")
        self.assertEqual(merged[0]["state"], "Massachusetts")
        agency = {"source": "mbta", "region": "Statewide agency", "town": "MBTA", "sector": "town"}
        agency["title"] = "Vinyl siding at a bus garage"
        _label_bids([agency], {})
        self.assertEqual(agency["sector"], "agency")
        self.assertEqual(agency["state"], "Massachusetts")
        self.assertEqual(agency["trade"], "cladding")
        panel = {"source": "town", "title": "Electrical panel upgrade", "category": "Building"}
        _label_bids([panel], {})
        self.assertIsNone(panel["trade"])
        self.assertIsNone(merged[0]["legal_notice_url"])
        upcoming = "https://bc.mbta.com/future/#haverhill"
        other = "https://bc.mbta.com/future/#quincy"
        self.assertNotEqual(canonical_url(upcoming), canonical_url(other))
        self.assertIn("haverhill", canonical_url(upcoming))


class NoticePageTest(unittest.TestCase):
    def test_document_links_skip_addenda_and_closed_headings(self):
        from scraper.parsers.notices import parse_notice_page as parse_html_notices

        html = """
        <main>
          <a href="https://cdn.example/bid631.pdf">Bid 631: On-Call Plumbing</a>
          <a href="https://cdn.example/add.pdf">Bid 631: Addendum #1</a>
          <a href="https://cdn.example/cancel.pdf">BID 629: On-Call Plumbing CANCELLED</a>
          <p>CLOSED</p>
          <a href="/Portals/56/pdf/floor.pdf">Solicitation for Bid - Floor Covering</a>
          <p>OPEN REQUEST FOR PROPOSALS</p>
          <a href="/Portals/56/RFPs/Solicitation-RFP-RAD.pdf">RAD CONSULTANT</a>
          <a href="https://app.heygov.com/town/forms/bid/send">Request Bid Document</a>
        </main>
        """
        items = parse_html_notices(html, "https://example.org/bids")
        titles = [item["title"] for item in items]
        self.assertIn("Bid 631: On-Call Plumbing", titles)
        self.assertIn("RAD CONSULTANT", titles)
        self.assertNotIn("Bid 631: Addendum #1", titles)
        self.assertFalse(any("CANCELLED" in title for title in titles))
        self.assertFalse(any("Floor" in title for title in titles))
        self.assertFalse(any("Request Bid" in title for title in titles))
        plumbing = next(item for item in items if "631" in item["title"])
        self.assertEqual(plumbing["bid_number"], "631")

    def test_dated_heading_without_a_document_link(self):
        from scraper.parsers.notices import parse_notice_page as parse_html_notices

        html = """
        <div class="sk-page-content">
          <strong>38 AND 34 WINTHROP STREET BUILDING STRUCTURE DEMOLITION</strong>
          <strong>Bid Opening November 19, 2026 @ 10:00 am</strong>
          <div>No current bids.</div>
        </div>
        """
        items = parse_html_notices(html, "https://medwayma.gov/rfpifb/")
        self.assertEqual(len(items), 1)
        self.assertIn("WINTHROP", items[0]["title"])
        self.assertTrue(items[0]["due"].startswith("2026-11-19"))
        self.assertIn("#", items[0]["url"])


class HostPoolTest(unittest.TestCase):
    def _fetcher(self, hosts):
        import scraper.fetch as fetch_mod
        from scraper.fetch import PoliteFetcher
        from scraper.robots import RobotsPolicy

        fetcher = PoliteFetcher()
        for host in hosts:
            fetcher._robots[host] = RobotsPolicy.allow_everything()
            fetcher._delay[host] = 10
            fetcher._last[host] = fetch_mod.time.monotonic()
            fetcher._openers[host] = _FakeOpener()
        return fetcher

    def _run_pair(self, fetcher, urls):
        import threading
        import time
        import scraper.fetch as fetch_mod

        order = []
        real_sleep = time.sleep

        def fake_sleep(_seconds):
            order.append("start")
            real_sleep(0.05)
            order.append("end")

        original = fetch_mod.time.sleep
        fetch_mod.time.sleep = fake_sleep
        try:
            threads = [threading.Thread(target=fetcher.get, args=(url,)) for url in urls]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(3)
                self.assertFalse(thread.is_alive())
        finally:
            fetch_mod.time.sleep = original
        return order

    def test_different_hosts_overlap_their_waits(self):
        fetcher = self._fetcher(("alpha.example", "beta.example"))
        order = self._run_pair(
            fetcher,
            ("https://alpha.example/a", "https://beta.example/b"),
        )
        first_end = order.index("end")
        self.assertGreaterEqual(order[:first_end].count("start"), 2)

    def test_same_host_waits_do_not_overlap(self):
        fetcher = self._fetcher(("alpha.example",))
        order = self._run_pair(
            fetcher,
            ("https://alpha.example/a", "https://alpha.example/b"),
        )
        self.assertEqual(order.index("end"), 1)
        self.assertEqual(order.count("start"), 2)


class _FakeOpener:
    def open(self, request, timeout=None):
        return _FakeResponse()


class _FakeResponse:
    status = 200
    headers = {"Content-Type": "text/html; charset=utf-8"}

    def geturl(self):
        return "https://alpha.example/a"

    def read(self, _n):
        return b"ok"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class InviteParseTest(unittest.TestCase):
    def test_synthetic_invites_get_region_state_and_cladding(self):
        import json
        import subprocess

        script = ROOT / "apps-script" / "test_parse.js"
        subprocess.check_call(["node", str(script)], cwd=str(ROOT))
        raw = subprocess.check_output(
            ["node", str(ROOT / "apps-script" / "parse_cli.js"), str(ROOT / "apps-script" / "fixtures")],
            cwd=str(ROOT),
            text=True,
        )
        parsed = json.loads(raw)
        regions = town_regions_from_config()
        bids = to_dashboard_bids(parsed, regions)
        by_title = {bid["title"]: bid for bid in bids}
        framingham = by_title["Framingham Office Curtain Wall"]
        self.assertEqual(framingham["sector"], "private")
        self.assertEqual(framingham["source"], "plan_room")
        self.assertEqual(framingham["town"], "Framingham")
        self.assertEqual(framingham["region"], "MetroWest core")
        self.assertEqual(framingham["state"], "Massachusetts")
        self.assertEqual(framingham["trade"], "cladding")
        self.assertTrue(framingham["due_date"].startswith("2026-11-12T14:00"))
        self.assertIn("Harborline Construction", framingham["category"])
        self.assertNotEqual(framingham["town"], "Concord")
        natick = by_title["Natick Lab Fit-Out"]
        self.assertEqual(natick["town"], "Natick")
        self.assertEqual(natick["region"], "MetroWest core")
        self.assertIsNone(natick["trade"])
        self.assertIn("Electrical panel", natick["category"])
        wellesley = by_title["Wellesley Library Window Replacement"]
        self.assertEqual(wellesley["town"], "Wellesley")
        self.assertEqual(wellesley["region"], "Next ring")
        self.assertEqual(wellesley["trade"], "cladding")
        self.assertEqual(wellesley["state"], "Massachusetts")
        partial = by_title["Invitation to Bid"]
        self.assertEqual(partial["sector"], "private")
        self.assertEqual(partial["town"], "")
        self.assertEqual(partial["region"], "")
        self.assertEqual(partial["due_date"], None)
        self.assertEqual(partial["url"], "")
        self.assertIsNone(partial["trade"])
        doubled = to_dashboard_bids(parsed + parsed, regions)
        self.assertEqual(len(doubled), len(bids))


class CladdingTest(unittest.TestCase):
    def test_envelope_words_match_and_bare_panel_does_not(self):
        self.assertEqual(cladding_trade("Vinyl siding replacement"), "cladding")
        self.assertEqual(cladding_trade("Curtain wall at the library"), "cladding")
        self.assertEqual(cladding_trade("Storefront renovation"), "cladding")
        self.assertEqual(cladding_trade("EIFS repair"), "cladding")
        self.assertEqual(cladding_trade("Fiber cement panels"), "cladding")
        self.assertEqual(cladding_trade("Façade restoration"), "cladding")
        self.assertEqual(cladding_trade("Window replacement"), "cladding")
        self.assertEqual(cladding_trade("Building envelope repairs"), "cladding")
        self.assertEqual(cladding_trade("ACM composite panel"), "cladding")
        self.assertEqual(cladding_trade("Brick veneer"), "cladding")
        self.assertIsNone(cladding_trade("Electrical panel upgrade"))
        self.assertIsNone(cladding_trade("Solar panel installation"))
        self.assertIsNone(cladding_trade("Catch basin cleaning"))
        self.assertIsNone(cladding_trade("Guardrail panel repair"))
        self.assertIsNone(cladding_trade("Residing in the district office"))


class AgencyFeedTest(unittest.TestCase):
    def test_massdot_keeps_covered_towns_and_drops_district_work(self):
        html = """
        <div class='sm_hilite'>Bid Opening: 11/3/2026 2:00PM</div>
        <table>
          <tr><td><strong>Location:</strong><i> NATICK</i></td></tr>
          <tr><td><strong>Description:</strong><i> Bridge Replacement, Boden Lane</i></td></tr>
          <tr><td><strong>Project Number:</strong> 607420</td></tr>
        </table>
        <table>
          <tr><td><strong>Location:</strong><i> DISTRICT5</i></td></tr>
          <tr><td><strong>Description:</strong><i> Cleaning of Drainage Structures</i></td></tr>
          <tr><td><strong>Project Number:</strong> 616408</td></tr>
        </table>
        <table>
          <tr><td><strong>Location:</strong><i> BOSTON</i></td></tr>
          <tr><td><strong>Description:</strong><i> Noise Barriers along Franklin Street</i></td></tr>
          <tr><td><strong>Project Number:</strong> 616308</td></tr>
        </table>
        <table>
          <tr><td><strong>Location:</strong><i> NORTHBOROUGH - SHREWSBURY</i></td></tr>
          <tr><td><strong>Description:</strong><i> Route 20 rehabilitation</i></td></tr>
          <tr><td><strong>Project Number:</strong> 608840</td></tr>
        </table>
        """
        rows = parse_massdot(html, "https://hwy.example/statusReport.asp", {"Natick": "Next ring", "Shrewsbury": "Next ring", "Franklin": "Outer ring"})
        numbers = [row["bid_number"] for row in rows]
        self.assertEqual(numbers, ["607420", "608840"])
        self.assertEqual(rows[0]["town"], "MassDOT")
        self.assertEqual(rows[0]["region"], "Statewide agency")
        self.assertEqual(rows[0]["sector"], "agency")
        self.assertIn("Natick", rows[0]["title"])
        self.assertTrue(rows[0]["due"].startswith("2026-11-03"))

    def test_dcr_ignores_the_awards_table(self):
        html = """
        <h2>Construction Contracts – Bid Responses Sought</h2>
        <table><tr><th>Contract #</th><th>Name</th><th>Date</th></tr>
        <tr><td>P27-3707-C1A</td><td>Bellevue Pond Dam Rehabilitation</td><td>10/29/2026</td></tr>
        <tr><td>P27-3713-M1A</td><td>Fence &amp; Guardrail Maintenance</td><td>12/3/2026</td></tr>
        </table>
        <h2>Recent Construction Contract Awards</h2>
        <table><tr><td>P26-1</td><td>Old roof award</td><td>1/2/2024</td></tr></table>
        """
        rows = parse_dcr(html, "https://www.mass.gov/info-details/dcr-contracts-and-procurement")
        self.assertEqual([row["bid_number"] for row in rows], ["P27-3707-C1A", "P27-3713-M1A"])
        self.assertEqual(rows[0]["town"], "DCR")
        self.assertEqual(rows[0]["region"], "Statewide agency")
        self.assertEqual(rows[1]["due"], "2026-12-03")

    def test_mapc_keeps_metrowest_and_drops_south_shore(self):
        html = """
        <div class="fl-module fl-module-rich-text fl-rich-text">
          <p><strong>Legal Notice of Invitation for Bids for Public Works Services - MW 2027 PW1</strong></p>
          <p>MetroWest consortium: Ashland, Hopkinton, Natick, and Wellesley.
          Sealed bids will be accepted until 3:00 PM, October 8, 2026 via Commbuys.</p>
          <p>Addendum - Bid Due Date Rescheduled to October 15, 2026 at 3:00 PM</p>
        </div>
        <div class="fl-module fl-module-rich-text fl-rich-text">
          <p><strong>Legal Notice of Invitation for Bids - SS 2027 PW1</strong></p>
          <p>South Shore towns: Braintree, Hingham, and Scituate. Bids due October 8, 2026.</p>
        </div>
        <div class="fl-module fl-module-rich-text fl-rich-text">
          <p><strong>Legal Notice - Franklin Street Garage</strong></p>
          <p>Boston Franklin Street garage waterproofing. Bids are due November 2, 2026.</p>
        </div>
        <div class="fl-module fl-module-rich-text fl-rich-text">
          <p><strong>Legal Notice of Request for Proposals - Old carshare</strong></p>
          <p>Framingham and Natick parking lots. Proposals are due by Monday, November 24 at 5:00pm.</p>
          <p><a href="/uploads/2025/10/carshare-10.27.2025.pdf">RFP</a></p>
        </div>
        <div class="fl-module fl-module-rich-text fl-rich-text">
          <p><strong><a href="/crhsac.pdf">CRHSAC24 - Worcester Area Interoperability</a></strong></p>
          <p>MAPC invites bids for communication equipment. Sealed bids will be opened on November 10, 2026 at 12:00PM.</p>
        </div>
        """
        regions = {"Natick": "Next ring", "Ashland": "MetroWest core", "Franklin": "Outer ring", "Wellesley": "Next ring"}
        rows = parse_mapc(html, "https://www.mapc.org/about-mapc/legal-notices-meetings/", regions)
        titles = [row["title"] for row in rows]
        self.assertTrue(any("MW 2027" in title for title in titles))
        self.assertTrue(any("Worcester" in title for title in titles))
        self.assertFalse(any("SS 2027" in title for title in titles))
        self.assertFalse(any("Franklin" in title for title in titles))
        self.assertFalse(any("carshare" in title.casefold() for title in titles))
        metro = next(row for row in rows if "MW 2027" in row["title"])
        self.assertEqual(metro["town"], "MAPC")
        self.assertEqual(metro["region"], "MetroWest core")
        self.assertEqual(metro["sector"], "agency")
        self.assertTrue(metro["due"].startswith("2026-10-15T15:00"))
        worcester = next(row for row in rows if "Worcester" in row["title"])
        self.assertEqual(worcester["region"], "Outer ring")
        self.assertTrue(worcester["due"].startswith("2026-11-10"))

    def test_commbuys_mapc_keeps_worcester_and_drops_south_shore(self):
        regions = {"Natick": "Next ring", "Needham": "Next ring"}
        self.assertFalse(mapc_in_range("SS 2027 PW2", regions))
        self.assertFalse(mapc_in_range("FCAM 2027 Ambulances", regions))
        self.assertTrue(mapc_in_range("CRHSAC24 Worcester Area Interoperability", regions))
        self.assertFalse(
            names_outside_place(
                "South Boston Neighborhood Working Group",
                {"Boston": "Cities", "Needham": "Next ring"},
            )
        )
        self.assertTrue(names_outside_place("Chelsea office renovation", {"Boston": "Cities"}))
        self.assertFalse(names_outside_place("Needham Dewatering Drainage Line", regions))
        rows = [
            {
                "Bid Solicitation #": "BD-SS",
                "Organization Name": "Metropolitan Area Planning Council",
                "Description": "SS 2027 PW2",
                "Alternate Id": "",
                "Bid Opening Date": "11/01/2026",
            },
            {
                "Bid Solicitation #": "BD-W",
                "Organization Name": "MAPC",
                "Description": "CRHSAC24 Worcester Area Interoperability System Upgrades",
                "Alternate Id": "",
                "Bid Opening Date": "11/10/2026",
            },
            {
                "Bid Solicitation #": "BD-BOS",
                "Organization Name": "Massachusetts Water Resources Authority",
                "Description": "Technical Assistance to the South Boston Neighborhood Working Group",
                "Alternate Id": "",
                "Bid Opening Date": "12/01/2026",
            },
            {
                "Bid Solicitation #": "BD-NEED",
                "Organization Name": "MWRA",
                "Description": "Needham Dewatering Drainage Line",
                "Alternate Id": "",
                "Bid Opening Date": "12/01/2026",
            },
        ]
        page = [{
            "town": "MAPC",
            "source": "mapc",
            "title": "CRHSAC24 - Worcester Area Interoperability System Upgrades",
            "bid_number": "CRHSAC24",
        }]
        extra = covered_rows(page, rows, regions)
        self.assertEqual(sorted(item["bid_number"] for item in extra), ["BD-BOS", "BD-NEED"])
        self.assertTrue(all(item["town"] == "MWRA" for item in extra))


class CityAndSamTest(unittest.TestCase):
    def test_city_parsers_and_boston_projects(self):
        boston = parse_boston_bids(
            """
            <div class="views-row">
              <div class="n-li-t"><a href="/bid-listings/ev1">Curtain wall at City Hall</a></div>
              <span class="dl-t">Due:</span><span class="dl-d">01/06/2027 - 5:00pm</span>
            </div>
            """,
            "https://www.boston.gov/bid-listings",
        )
        self.assertEqual(boston[0]["title"], "Curtain wall at City Hall")
        self.assertTrue(boston[0]["due"].startswith("2027-01-06"))
        projects = parse_boston_projects(
            """
            <table class='devprojectTable projectTable'>
              <caption><a href='/projects/development-projects/liberty'>Liberty Mutual</a></caption>
              <h2>Letter of Intent<span>Project Status</span></h2>
              <h2><span>Project Type</span></h2>
              <h2>Oct 14 2026</h2>
            </table>
            <table class='devprojectTable projectTable'>
              <caption><a href='/projects/development-projects/house'>295 Waldemar</a></caption>
              <h2>Under Review<span>Project Status</span></h2>
              <h2>Rental / Residential<span>Project Type</span></h2>
              <h2>Oct 16 2026</h2>
            </table>
            <table class='devprojectTable projectTable'>
              <caption><a href='/projects/development-projects/lab'>Kendall Lab</a></caption>
              <h2>Under Review<span>Project Status</span></h2>
              <h2>Commercial / Lab<span>Project Type</span></h2>
              <h2>Oct 10 2026</h2>
            </table>
            """,
            "https://www.bostonplans.org/projects/development-projects",
        )
        names = [row["title"] for row in projects]
        self.assertIn("Liberty Mutual", names)
        self.assertIn("Kendall Lab", names)
        self.assertNotIn("295 Waldemar", names)
        self.assertTrue(all(row["kind"] == "lead" for row in projects))

    def test_sam_fixture_keeps_construction_solicitations(self):
        regions = town_regions_from_config()
        records = [
            {
                "noticeId": "keep-ma",
                "title": "Siding replacement, curtain wall",
                "solicitationNumber": "W912WJ-26-B-0001",
                "type": "Solicitation",
                "baseType": "Solicitation",
                "active": "Yes",
                "naicsCode": "238170",
                "responseDeadLine": "12/15/2026",
                "uiLink": "https://sam.gov/opp/keep-ma/view",
                "department": "DEPT OF DEFENSE",
                "placeOfPerformance": {"city": {"name": "Natick"}, "state": {"code": "MA"}},
            },
            {
                "noticeId": "award",
                "title": "Awarded roof",
                "type": "Award Notice",
                "baseType": "Solicitation",
                "active": "Yes",
                "naicsCode": "236220",
                "placeOfPerformance": {"city": {"name": "Boston"}, "state": {"code": "MA"}},
            },
            {
                "noticeId": "goods",
                "title": "Office supplies",
                "type": "Solicitation",
                "active": "Yes",
                "naicsCode": "339940",
                "placeOfPerformance": {"city": {"name": "Boston"}, "state": {"code": "MA"}},
            },
            {
                "noticeId": "nashua",
                "title": "Hangar repair",
                "type": "Solicitation",
                "active": "Yes",
                "naicsCode": "236220",
                "uiLink": "https://sam.gov/opp/nashua/view",
                "placeOfPerformance": {"city": {"name": "Nashua"}, "state": {"code": "NH"}},
            },
            {
                "noticeId": "manchester",
                "title": "Remote hangar",
                "type": "Solicitation",
                "active": "Yes",
                "naicsCode": "236220",
                "placeOfPerformance": {"city": {"name": "Manchester"}, "state": {"code": "NH"}},
            },
        ]
        rows = filter_opportunities(records, regions)
        ids = {row["bid_number"] or row["url"] for row in rows}
        self.assertEqual(len(rows), 2)
        kept = {row["title"] for row in rows}
        self.assertIn("Siding replacement, curtain wall", kept)
        self.assertIn("Hangar repair", kept)
        natick = next(row for row in rows if "Siding" in row["title"])
        self.assertEqual(natick["town"], "Natick")
        self.assertEqual(natick["sector"], "federal")
        nashua = next(row for row in rows if "Hangar" in row["title"])
        self.assertEqual(nashua["town"], "Nashua")
        self.assertEqual(nashua["state_code"], "NH")
        from scraper.scrape import _sam_item
        from scraper.trades import cladding_trade
        labeled = _sam_item(natick, regions)
        self.assertEqual(labeled["region"], "MetroWest core")
        self.assertEqual(labeled["source"], "sam_gov")
        self.assertEqual(cladding_trade(labeled["title"], labeled["category"]), "cladding")
        skipped = search_opportunities(None, datetime(2026, 10, 28, tzinfo=EASTERN), api_key="")
        self.assertEqual(skipped[1], "skipped")
        self.assertIsNone(skipped[0])

    def test_agenda_lines_keep_construction_items(self):
        text = """
        Approval of minutes
        Site plan review for a new commercial building at 10 Main Street
        Pledge of allegiance
        Special permit for mixed-use development
        Dog license hearing
        """
        lines = lead_lines(text)
        self.assertEqual(len(lines), 2)
        self.assertTrue(any("commercial" in line.casefold() for line in lines))
        self.assertFalse(agenda_links("<p>No files</p>", "https://example.gov/AgendaCenter"))


if __name__ == "__main__":
    unittest.main()
