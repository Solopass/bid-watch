"""Scrape covered-town bid pages into data/bids.json and data/status.json.

One pass. A town that fails is recorded and the rest of the run continues.
COMMBUYS is ingested only for the covered towns and their related agencies.
The Central Register is recorded as skipped unless a robots-allowed bulletin
feed is available.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.commbuys import (
    SEARCH_URL,
    CommBuysError,
    assign_flags,
    covered_rows,
    fetch_open_solicitations,
)
from scraper.legal import SEARCH_URL as LEGAL_SEARCH_URL
from scraper.legal import LegalNoticeError, apply_notices, fetch_notices
from scraper.places import town_regions_from_config
from scraper.dates import EASTERN
from scraper.fetch import DEFAULT_UA, FetchError, PoliteFetcher, RobotsDisallowed
from scraper.parsers.bidnet import ParseError as BidNetParseError
from scraper.parsers.bidnet import parse_bidnet
from scraper.parsers.civicplus import (
    ParseError as CivicParseError,
    close_kind,
    enrich_civicplus_detail,
    parse_civicplus,
)
from scraper.parsers.bidexpress import ParseError as BidExpressParseError
from scraper.parsers.bidexpress import parse_bid_express
from scraper.parsers.documents import ParseError as DocParseError
from scraper.parsers.documents import parse_document_page
from scraper.parsers.massdevelopment import ParseError as MassDevParseError
from scraper.parsers.massdevelopment import parse_massdevelopment
from scraper.parsers.mbta import ParseError as MbtaParseError
from scraper.parsers.mbta import due_from_detail, parse_current_solicitations, parse_future_solicitations
from scraper.parsers.msba import ParseError as MsbaParseError
from scraper.parsers.msba import latest_bid_schedule_url, parse_bid_schedule
from scraper.parsers.agencies import ParseError as AgencyParseError
from scraper.parsers.agencies import parse_dcr, parse_mapc, parse_massdot
from scraper.parsers.agendas import MAX_AGENDA_PDFS, agenda_links, lead_lines
from scraper.parsers.cities import ParseError as CityParseError
from scraper.parsers.cities import (
    parse_bha_bids,
    parse_boston_bids,
    parse_boston_projects,
    parse_quincy_bids,
    parse_somerville_bids,
    parse_worcester_bids,
)
from scraper.pdftext import pdf_to_text
from scraper.samgov import SEARCH_URL as SAM_SEARCH_URL
from scraper.samgov import SamError, filter_opportunities, search_opportunities
from scraper.parsers.notices import ParseError as NoticeParseError
from scraper.parsers.notices import parse_notice_page
from scraper.trades import cladding_trade
from scraper.parsers.wordpress import ParseError as WpParseError
from scraper.parsers.wordpress import enrich_wordpress_post, parse_wordpress_list
from scraper.store import (
    canonical_url,
    load_document,
    load_status,
    merge_bids,
    new_open_bids,
    norm_bid_number,
    sort_bids,
    write_json,
)

# The State Library aggregation page is allowed, but the bulletin list is the
# items tab robots.txt disallows (/entities/*/*/*), and the discover search
# API is disallowed too. OAI answers Identify and then lists no records.
# RSS for the collection 404s. Sitemaps are allowed untitled entity URLs,
# about 100MB, not a bulletin feed. sec.state.ma.us is not requested.
CENTRAL_REGISTER_SKIPPED = (
    "Skipped: no robots-allowed feed for the latest bulletin. "
    "The State Library items list and search are disallowed; "
    "OAI returns no records; RSS is absent. sec.state.ma.us was not requested."
)

PARSE_ERRORS = (
    CivicParseError,
    DocParseError,
    WpParseError,
    BidNetParseError,
    BidExpressParseError,
    MassDevParseError,
    MbtaParseError,
    MsbaParseError,
    NoticeParseError,
    LegalNoticeError,
    AgencyParseError,
    CityParseError,
)
STATE_BY_REGION = {
    "RI border": "Rhode Island",
    "NH border": "New Hampshire",
    "CT border": "Connecticut",
}
AGENCY_SOURCES = {"bid_express", "massdevelopment", "mbta", "msba"}
FEED_LABELS = {
    "legal_notices": "Legal notices",
    "bid_express": "Bid Express",
    "massdevelopment": "MassDevelopment",
    "mbta": "MBTA",
    "msba": "MSBA",
    "massdot": "MassDOT",
    "dcr": "DCR",
    "mapc": "MAPC",
    "sam_gov": "SAM.gov",
    "boston_planning": "Boston Planning",
}
CHALLENGE_TITLES = (
    "just a moment",
    "attention required",
    "human verification",
    "access denied",
    "are you a robot",
)


def main() -> int:
    started = time.perf_counter()
    config = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    town_regions = town_regions_from_config(config)
    bids_path = ROOT / "data" / "bids.json"
    status_path = ROOT / "data" / "status.json"
    new_path = ROOT / "data" / "new_bids.json"
    previous_doc = load_document(bids_path)
    previous_status = load_status(status_path)
    known = {
        canonical_url(record.get("url") or ""): record
        for record in previous_doc.get("bids", [])
    }
    fetcher = PoliteFetcher(
        user_agent=config.get("user_agent") or DEFAULT_UA,
        min_interval=int(config.get("min_interval_seconds") or 10),
    )
    now = datetime.now(EASTERN)
    fresh: list[dict] = []
    source_reports: list[dict] = []
    successful: set[str] = set()
    workers = max(1, int(config.get("workers") or 8))
    feeds = config.get("feeds") or []
    previous_feeds = previous_status.get("feeds") or {}

    # Different hosts run together. The fetcher still waits 10 seconds
    # between requests to the same host.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        town_futures = [
            pool.submit(_scrape_listed_source, source, fetcher, known)
            for source in config["sources"]
        ]
        feed_futures = [
            pool.submit(
                _scrape_one_feed,
                feed,
                fetcher,
                known,
                town_regions,
                previous_feeds,
                now,
            )
            for feed in feeds
        ]
        commbuys_future = pool.submit(_run_commbuys, fetcher, previous_status, now)
        town_results = [future.result() for future in town_futures]
        feed_results = [future.result() for future in feed_futures]
        commbuys_rows, commbuys_report = commbuys_future.result()

    for report, found in town_results:
        source_reports.append(report)
        if report["status"] == "ok":
            successful.add(report["url"])
            for extra in report.get("also_successful") or []:
                successful.add(extra)
            fresh.extend(found)

    feed_items, feed_ok, feed_reports, legal_notices = _finish_feeds(
        feed_results, previous_feeds, now
    )

    # Merge town pages first. COMMBUYS rows that match a town bid, open or
    # closed, update that bid instead of being copied. Unmatched covered
    # rows are then merged with the COMMBUYS search marked successful, so a
    # covered solicitation that leaves the CSV is closed and a failed export
    # leaves the previous COMMBUYS rows in place.
    merged = merge_bids(previous_doc.get("bids", []), fresh, successful, now)
    added = 0
    if commbuys_rows is not None:
        extras = covered_rows([*merged, *feed_items], commbuys_rows, town_regions)
        added = len(extras)
        fresh.extend(extras)
        successful.add(canonical_url(SEARCH_URL))
    if legal_notices is not None:
        fresh_keys = {
            _key(item)
            for item in fresh
            if (item.get("source") or "town") in ("town", "commbuys")
        }
        pool = [
            item
            for item in fresh
            if (item.get("source") or "town") in ("town", "commbuys")
        ]
        for record in previous_doc.get("bids", []):
            if (record.get("source") or "town") not in ("town", "commbuys"):
                continue
            if _key(record) in fresh_keys:
                continue
            pool.append(record)
        unmatched = apply_notices(pool, legal_notices)
        matched_notices = len(legal_notices) - len(unmatched)
        fresh.extend(unmatched)
        successful.add(canonical_url(LEGAL_SEARCH_URL))
        legal_report = feed_reports.get("legal_notices") or {}
        legal_report["message"] = (
            f"{legal_report.get('message')}; {matched_notices} matched an existing bid; "
            f"{len(unmatched)} added"
        )
    fresh.extend(feed_items)
    successful.update(feed_ok)
    merged = merge_bids(previous_doc.get("bids", []), fresh, successful, now)
    _carry_commbuys(merged, previous_doc.get("bids", []))
    assign_flags(merged, commbuys_rows, town_regions)
    _label_bids(merged, town_regions)
    if commbuys_rows is not None and commbuys_report.get("status") == "ok":
        matched = sum(1 for bid in merged if bid.get("on_commbuys") == "yes" and bid.get("source") != "commbuys")
        commbuys_report["message"] = (
            f"{len(commbuys_rows)} open statewide; {added} covered rows kept; "
            f"{matched} matched a town bid"
        )
        commbuys_report["covered_rows"] = added
    town_order = []
    for source in config["sources"]:
        if source["town"] not in town_order:
            town_order.append(source["town"])
    for bid in merged:
        if bid.get("town") not in town_order:
            town_order.append(bid["town"])
    merged = sort_bids(merged, town_order)
    opened = new_open_bids(previous_doc.get("bids", []), merged)

    bids_doc = {"updated": _iso(now), "bids": merged}
    write_json(bids_path, bids_doc)
    status_doc = {
        "updated": _iso(now),
        "commbuys": commbuys_report,
        "central_register": _central_register_status(),
        "feeds": _feed_counts(feed_reports, merged),
        "towns": _town_status(
            [source["town"] for source in config["sources"]],
            source_reports,
            merged,
            previous_status,
            now,
            town_regions,
        ),
    }
    write_json(status_path, status_doc)
    write_json(
        new_path,
        {
            "date": now.date().isoformat(),
            "bids": [
                {
                    "town": bid["town"],
                    "region": bid.get("region"),
                    "source": bid.get("source"),
                    "kind": bid.get("kind") or "open",
                    "title": bid["title"],
                    "bid_number": bid.get("bid_number"),
                    "due_date": bid.get("due_date"),
                    "url": bid["url"],
                }
                for bid in opened
            ],
        },
    )
    _print_summary(town_order, merged, status_doc, opened, time.perf_counter() - started)
    return 0


def _scrape_listed_source(source: dict, fetcher: PoliteFetcher, known: dict):
    report = {
        "town": source["town"],
        "type": source["type"],
        "url": canonical_url(source["url"]),
        "status": "ok",
        "count": 0,
        "message": None,
    }
    try:
        if source.get("type") == "skipped":
            report["status"] = "skipped"
            report["message"] = source.get("notes") or "Skipped"
            print(f"SKIP {source['town']}: {report['message']}", flush=True)
            return report, []
        found = scrape_source(source, fetcher, known)
        if source.get("_agenda_ok"):
            report["also_successful"] = [source["_agenda_ok"]]
    except (FetchError, RobotsDisallowed, *PARSE_ERRORS) as exc:
        report["status"] = "error"
        report["message"] = str(exc)
        print(f"ERROR {source['town']} ({source['type']}): {exc}", flush=True)
        return report, []
    except Exception as exc:  # a parser bug should not sink the other towns
        report["status"] = "error"
        report["message"] = f"{type(exc).__name__}: {exc}"
        print(f"ERROR {source['town']} ({source['type']}): {exc}", flush=True)
        traceback.print_exc()
        return report, []
    report["count"] = len(found)
    print(f"OK {source['town']} ({source['type']}): {len(found)} listing(s)", flush=True)
    return report, found


def _run_commbuys(fetcher, previous_status, now):
    report = _commbuys_status(previous_status, now)
    try:
        rows = fetch_open_solicitations(fetcher)
    except (CommBuysError, FetchError, RobotsDisallowed) as exc:
        report["status"] = "error"
        report["message"] = str(exc)
        report["last_success"] = (previous_status.get("commbuys") or {}).get("last_success")
        report["last_error"] = _iso(now)
        print(f"COMMBUYS check failed ({exc}); flags left unknown where unset", flush=True)
        return None, report
    except Exception as exc:
        report["status"] = "error"
        report["message"] = f"{type(exc).__name__}: {exc}"
        report["last_success"] = (previous_status.get("commbuys") or {}).get("last_success")
        report["last_error"] = _iso(now)
        print(f"COMMBUYS check failed ({exc}); flags left unknown where unset", flush=True)
        traceback.print_exc()
        return None, report
    print(f"OK COMMBUYS: {len(rows)} open solicitations", flush=True)
    return rows, {
        "status": "ok",
        "last_success": _iso(now),
        "last_error": None,
        "message": f"{len(rows)} open solicitations",
        "rows": len(rows),
    }


def _scrape_one_feed(feed, fetcher, known, town_regions, previous_feeds, now) -> dict:
    """One statewide page. Legal notices stay unmatched until the main thread."""
    kind = feed.get("type")
    url = feed.get("url") or ""
    agency = feed.get("agency") or kind
    result = {
        "kind": kind,
        "agency": agency,
        "items": [],
        "ok_url": None,
        "part": None,
        "error": None,
        "report": None,
        "legal": None,
    }
    try:
        if kind == "bid_express":
            html = fetcher.get(url)
            _reject_challenge(html, url)
            found = parse_bid_express(html, url, agency)
            result["items"] = found
            result["ok_url"] = canonical_url(url)
            result["part"] = f"{agency} {len(found)}"
        elif kind == "massdevelopment":
            html = fetcher.get(url)
            _reject_challenge(html, url)
            found = parse_massdevelopment(html, url)
            result["items"] = found
            result["ok_url"] = canonical_url(url)
            result["report"] = _feed_ok(now, "massdevelopment", f"{len(found)} posting(s)")
        elif kind == "mbta_current":
            html = fetcher.get(url)
            _reject_challenge(html, url)
            found = parse_current_solicitations(html, url)
            _finish_mbta(found, fetcher, known)
            result["items"] = found
            result["ok_url"] = canonical_url(url)
            result["part"] = f"{len(found)} current"
        elif kind == "mbta_future":
            html = fetcher.get(url)
            _reject_challenge(html, url)
            found = parse_future_solicitations(html, url)
            result["items"] = found
            result["ok_url"] = canonical_url(url)
            result["part"] = f"{len(found)} upcoming"
        elif kind == "msba":
            found, refreshed, pdf_url, message = _scrape_msba(
                feed, fetcher, town_regions, previous_feeds.get("msba") or {}
            )
            report = _feed_ok(now, "msba", message)
            report["pdf_url"] = pdf_url
            result["report"] = report
            if refreshed:
                result["items"] = found
                result["ok_url"] = canonical_url(url)
        elif kind == "legal_notice":
            notices, message = fetch_notices(fetcher, town_regions)
            result["legal"] = notices
            result["report"] = _feed_ok(now, "legal_notices", message)
        elif kind == "massdot":
            html = fetcher.get(url)
            _reject_challenge(html, url)
            found = parse_massdot(html, url, town_regions)
            result["items"] = found
            result["ok_url"] = canonical_url(url)
            result["report"] = _feed_ok(now, "massdot", f"{len(found)} in-range project(s)")
        elif kind == "dcr":
            html = fetcher.get(url)
            _reject_challenge(html, url)
            found = parse_dcr(html, url)
            result["items"] = found
            result["ok_url"] = canonical_url(url)
            result["report"] = _feed_ok(now, "dcr", f"{len(found)} current construction ad(s)")
        elif kind == "mapc":
            html = fetcher.get(url)
            _reject_challenge(html, url)
            found = parse_mapc(html, url, town_regions)
            result["items"] = found
            result["ok_url"] = canonical_url(url)
            result["report"] = _feed_ok(now, "mapc", f"{len(found)} in-range notice(s)")
        elif kind == "boston_planning":
            html = fetcher.get(url)
            _reject_challenge(html, url)
            found = parse_boston_projects(html, url)
            result["items"] = found
            result["ok_url"] = canonical_url(url)
            result["report"] = _feed_ok(now, "boston_planning", f"{len(found)} early lead(s)")
        elif kind == "sam_gov":
            records, status, message = search_opportunities(fetcher, now)
            if status == "skipped":
                result["report"] = {
                    "status": "skipped",
                    "label": FEED_LABELS["sam_gov"],
                    "last_success": None,
                    "last_error": None,
                    "message": message,
                    "open_count": 0,
                }
            else:
                found = []
                for item in filter_opportunities(records or [], town_regions):
                    found.append(_sam_item(item, town_regions))
                result["items"] = found
                result["ok_url"] = canonical_url(SAM_SEARCH_URL)
                result["report"] = _feed_ok(now, "sam_gov", f"{message}; {len(found)} kept")
        else:
            raise FetchError(f"unknown feed type {kind!r}")
    except (FetchError, RobotsDisallowed, SamError, *PARSE_ERRORS) as exc:
        result["error"] = str(exc)
        print(f"ERROR {agency} ({kind}): {exc}", flush=True)
    except Exception as exc:
        traceback.print_exc()
        result["error"] = f"{type(exc).__name__}: {exc}"
        print(f"ERROR {agency} ({kind}): {exc}", flush=True)
    else:
        print(f"OK {agency} ({kind})", flush=True)
    return result


def _finish_feeds(results: list[dict], previous_feeds: dict, now):
    items: list[dict] = []
    ok_sources: set[str] = set()
    reports: dict[str, dict] = {}
    legal_notices = None
    grouped: dict[str, dict] = {
        "bid_express": {"parts": [], "errors": []},
        "mbta": {"parts": [], "errors": []},
    }
    for result in results:
        kind = result["kind"]
        if result["error"]:
            _note_feed_error(
                grouped,
                reports,
                previous_feeds,
                now,
                kind,
                result["agency"],
                result["error"],
            )
            continue
        items.extend(result["items"])
        if result["ok_url"]:
            ok_sources.add(result["ok_url"])
        if result["part"] and kind == "bid_express":
            grouped["bid_express"]["parts"].append(result["part"])
        elif result["part"] and kind in ("mbta_current", "mbta_future"):
            grouped["mbta"]["parts"].append(result["part"])
        if result["report"]:
            key = "legal_notices" if kind == "legal_notice" else kind
            reports[key] = result["report"]
        if result["legal"] is not None:
            legal_notices = result["legal"]
    for key in ("bid_express", "mbta"):
        parts = grouped[key]["parts"]
        errors = grouped[key]["errors"]
        if not parts and not errors:
            continue
        message = "; ".join(parts + errors)
        if parts:
            report = _feed_ok(now, key, message)
            if errors:
                report["status"] = "error"
                report["last_error"] = _iso(now)
            reports[key] = report
        else:
            reports[key] = _feed_failed(previous_feeds, now, key, message)
    return items, ok_sources, reports, legal_notices


def _scrape_msba(feed, fetcher, town_regions, prior: dict):
    page_url = feed["url"]
    html = fetcher.get(page_url)
    _reject_challenge(html, page_url)
    pdf_url = latest_bid_schedule_url(html, page_url)
    if not pdf_url:
        raise FetchError("MSBA capital planning page had no estimated bid schedule")
    pdf_url = canonical_url(pdf_url).split("#", 1)[0]
    if prior.get("pdf_url") == pdf_url and prior.get("status") == "ok":
        return [], False, pdf_url, "Estimated bid schedule unchanged; PDF not downloaded"
    data, _final = fetcher.get_bytes(pdf_url)
    found = parse_bid_schedule(data, pdf_url, page_url, town_regions)
    return found, True, pdf_url, f"{len(found)} covered-town projects"


def _finish_mbta(items: list[dict], fetcher: PoliteFetcher, known: dict) -> None:
    opened = 0
    for item in items:
        if item.get("due"):
            continue
        prior = known.get(canonical_url(item["url"]))
        if prior and prior.get("due_date"):
            item["due"] = prior["due_date"]
            item["bid_number"] = item.get("bid_number") or prior.get("bid_number")
            continue
        if opened >= 12:
            print("MBTA detail cap reached; remaining due dates left blank", flush=True)
            return
        opened += 1
        try:
            html = fetcher.get(item["url"])
        except RobotsDisallowed:
            print(f"detail skipped, robots.txt disallows {item['url']}", flush=True)
            continue
        except FetchError as exc:
            print(f"detail failed {item['url']}: {exc}", flush=True)
            continue
        if _is_challenge(html):
            print(f"detail asked for a human check; not attempted ({item['url']})", flush=True)
            continue
        due = due_from_detail(html)
        if due:
            item["due"] = due


def _note_feed_error(grouped, reports, previous_feeds, now, kind, agency, exc) -> None:
    if kind == "bid_express":
        grouped["bid_express"]["errors"].append(f"{agency}: {exc}")
        return
    if kind in ("mbta_current", "mbta_future"):
        grouped["mbta"]["errors"].append(f"{agency}: {exc}")
        return
    key = {"msba": "msba", "legal_notice": "legal_notices"}.get(kind, kind or "feed")
    reports[key] = _feed_failed(previous_feeds, now, key, str(exc))


def _feed_ok(now, key: str, message: str) -> dict:
    return {
        "status": "ok",
        "label": FEED_LABELS.get(key, key),
        "last_success": _iso(now),
        "last_error": None,
        "message": message,
        "open_count": 0,
    }


def _feed_failed(previous_feeds: dict, now, key: str, message: str) -> dict:
    prior = previous_feeds.get(key) or {}
    report = {
        "status": "error",
        "label": FEED_LABELS.get(key, key),
        "last_success": prior.get("last_success"),
        "last_error": _iso(now),
        "message": message,
        "open_count": prior.get("open_count") or 0,
    }
    if prior.get("pdf_url"):
        report["pdf_url"] = prior["pdf_url"]
    return report


def _feed_counts(reports: dict, bids: list[dict]) -> dict:
    source_for = {
        "legal_notices": "legal_notice",
        "bid_express": "bid_express",
        "massdevelopment": "massdevelopment",
        "mbta": "mbta",
        "msba": "msba",
        "massdot": "massdot",
        "dcr": "dcr",
        "mapc": "mapc",
        "sam_gov": "sam_gov",
        "boston_planning": "boston_planning",
    }
    cleaned = {}
    for key in FEED_LABELS:
        if key not in reports:
            continue
        report = dict(reports[key])
        source = source_for[key]
        report["open_count"] = sum(
            1 for bid in bids if bid.get("source") == source and bid.get("status") == "open"
        )
        report.pop("pdf_url", None) if key != "msba" else None
        cleaned[key] = report
    return cleaned


def scrape_source(source: dict, fetcher: PoliteFetcher, known: dict) -> list[dict]:
    page_url = source["url"]
    html = fetcher.get(page_url)
    _reject_challenge(html, page_url)
    kind = source["type"]
    options = source.get("options") or {}
    if kind == "civicplus":
        listings = parse_civicplus(html, page_url)
        listings = [
            item
            for item in (
                _finish_civicplus(item, options, fetcher, known) for item in listings
            )
            if item
        ]
    elif kind == "bidnet":
        listings = parse_bidnet(html, page_url)
    elif kind == "wordpress":
        listings = parse_wordpress_list(html, page_url)
        listings = _finish_wordpress(listings, options, fetcher, known)
    elif kind == "documents":
        listings = parse_document_page(html, page_url)
    elif kind == "notices":
        listings = parse_notice_page(html, page_url)
    elif kind == "boston":
        listings = parse_boston_bids(html, page_url)
    elif kind == "worcester":
        listings = parse_worcester_bids(html, page_url)
    elif kind == "somerville":
        listings = parse_somerville_bids(html, page_url)
    elif kind == "quincy":
        listings = parse_quincy_bids(html, page_url)
    elif kind == "bha":
        listings = parse_bha_bids(html, page_url)
    else:
        raise FetchError(f"unknown page type {kind!r}")
    if kind == "civicplus":
        leads, agenda_url = _planning_leads(source, page_url, fetcher)
        if agenda_url:
            source["_agenda_ok"] = agenda_url
        listings.extend(leads)
    source_url = canonical_url(page_url)
    for item in listings:
        item["town"] = source["town"]
        item["region"] = source.get("region")
        item["state"] = item.get("state") or source.get("state") or STATE_BY_REGION.get(
            source.get("region") or "", "Massachusetts"
        )
        if item.get("kind") == "lead":
            item["sector"] = item.get("sector") or "private"
            item["source"] = item.get("source") or "planning_board"
            item["source_url"] = canonical_url(item.get("source_url") or source_url)
        else:
            item["sector"] = source.get("sector") or "town"
            item["source"] = "town"
            item["kind"] = item.get("kind") or "open"
            item["source_url"] = source_url
        item["url"] = canonical_url(item["url"])
        item.pop("close_raw", None)
        item.pop("status_raw", None)
    return listings


def _planning_leads(source: dict, page_url: str, fetcher: PoliteFetcher):
    """One Agenda Center page per CivicPlus town. No PDF when the HTML has no link."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(page_url)
    agenda_url = urlunsplit((parts.scheme, parts.netloc, "/AgendaCenter", "", ""))
    try:
        html = fetcher.get(agenda_url)
    except (FetchError, RobotsDisallowed) as exc:
        print(f"agenda skipped {source['town']}: {exc}", flush=True)
        return [], None
    if _is_challenge(html):
        print(f"agenda asked for a human check; not read ({source['town']})", flush=True)
        return [], None
    leads = []
    pdfs = getattr(fetcher, "agenda_pdfs", 0)
    for link in agenda_links(html, agenda_url)[:1]:
        if pdfs >= MAX_AGENDA_PDFS:
            print("agenda PDF cap reached", flush=True)
            break
        try:
            data, _final = fetcher.get_bytes(link["url"])
        except (FetchError, RobotsDisallowed) as exc:
            print(f"agenda file skipped {link['url']}: {exc}", flush=True)
            continue
        pdfs += 1
        fetcher.agenda_pdfs = pdfs
        if data[:5].startswith(b"%PDF") or link["url"].lower().endswith(".pdf"):
            text = pdf_to_text(data)
        else:
            text = data.decode("utf-8", "replace")
        for line in lead_lines(text):
            leads.append(
                {
                    "title": line,
                    "url": link["url"],
                    "kind": "lead",
                    "sector": "private",
                    "source": "planning_board",
                    "category": link["label"],
                    "posted_date": link.get("posted"),
                    "source_url": agenda_url,
                }
            )
    return leads, canonical_url(agenda_url)


def _sam_item(item: dict, town_regions: dict[str, str]) -> dict:
    town = item.get("town") or "Federal"
    code = item.get("state_code") or ""
    region = town_regions.get(town) or ""
    if not region:
        if code == "MA" and town.casefold() in {
            "boston", "worcester", "cambridge", "somerville", "quincy", "lowell"
        }:
            region = "Cities"
        elif code == "NH":
            region = "NH border"
        elif code == "RI":
            region = "RI border"
        else:
            region = "Statewide agency"
    state = {"MA": "Massachusetts", "NH": "New Hampshire", "RI": "Rhode Island"}.get(
        code, "Massachusetts"
    )
    if region in STATE_BY_REGION:
        state = STATE_BY_REGION[region]
    category = item.get("category")
    scope = item.get("scope") or ""
    if scope:
        category = f"{category} — {scope[:240]}" if category else scope[:240]
    return {
        "town": town,
        "region": region,
        "sector": "federal",
        "state": state,
        "source": "sam_gov",
        "kind": "open",
        "title": item["title"],
        "bid_number": item.get("bid_number"),
        "category": category,
        "posted_date": item.get("posted_date"),
        "due": item.get("due"),
        "url": item["url"],
        "source_url": SAM_SEARCH_URL,
    }


def _finish_civicplus(item: dict, options: dict, fetcher: PoliteFetcher, known: dict):
    kind = close_kind(item)
    when_blank = options.get("when_blank", "detail")
    when_contract = options.get("when_contract", "skip")
    need_detail = False
    if kind == "dated":
        need_detail = not item.get("bid_number") and bool(options.get("detail_if_missing_number"))
    elif kind in ("blank", "other"):
        if when_blank == "skip":
            return None
        need_detail = when_blank == "detail"
    elif kind == "contract":
        if when_contract == "skip":
            return None
        need_detail = when_contract == "detail"
    else:
        return None

    prior = known.get(canonical_url(item["url"]))
    if need_detail and prior:
        if kind != "dated" and prior.get("due_date"):
            item["due"] = prior["due_date"]
            item["posted_date"] = item.get("posted_date") or prior.get("posted_date")
            item["bid_number"] = item.get("bid_number") or prior.get("bid_number")
            need_detail = False
        elif kind == "dated" and prior.get("bid_number"):
            item["bid_number"] = prior["bid_number"]
            item["posted_date"] = item.get("posted_date") or prior.get("posted_date")
            need_detail = False

    if need_detail:
        extra = _read_detail(fetcher, item["url"], enrich_civicplus_detail)
        if extra:
            if extra.get("due"):
                item["due"] = extra["due"]
            if extra.get("posted_date"):
                item["posted_date"] = extra["posted_date"]
            if extra.get("bid_number") and not item.get("bid_number"):
                item["bid_number"] = extra["bid_number"]
    if kind != "dated" and not item.get("due"):
        return None
    return item


def _finish_wordpress(listings: list[dict], options: dict, fetcher: PoliteFetcher, known: dict):
    cap = int(options.get("max_posts") or 25)
    ordered = sorted(listings, key=lambda item: item.get("posted_date") or "", reverse=True)
    if len(ordered) > cap:
        print(f"WordPress list has {len(ordered)} posts; reading the {cap} newest", flush=True)
        ordered = ordered[:cap]
    kept = []
    for item in ordered:
        prior = known.get(canonical_url(item["url"]))
        if prior and prior.get("due_date"):
            item["due"] = prior["due_date"]
            item["bid_number"] = item.get("bid_number") or prior.get("bid_number")
            kept.append(item)
            continue
        extra = _read_detail(fetcher, item["url"], enrich_wordpress_post)
        if extra:
            item["due"] = extra.get("due")
            item["bid_number"] = extra.get("bid_number")
        kept.append(item)
    return kept


def _read_detail(fetcher: PoliteFetcher, url: str, enrich):
    try:
        html = fetcher.get(url)
    except RobotsDisallowed as exc:
        print(f"detail skipped, robots.txt disallows {url}", flush=True)
        return None
    except FetchError as exc:
        print(f"detail failed {url}: {exc}", flush=True)
        return None
    if _is_challenge(html):
        print(f"detail asked for a human check; not attempted ({url})", flush=True)
        return None
    return enrich(html)


def _reject_challenge(html: str, url: str) -> None:
    if _is_challenge(html):
        raise FetchError(f"{url} asked for a human check; not attempted")


def _is_challenge(html: str) -> bool:
    import re

    match = re.search(r"(?is)<title>(.*?)</title>", html)
    if not match:
        return False
    title = re.sub(r"\s+", " ", match.group(1)).strip().lower()
    return any(phrase in title for phrase in CHALLENGE_TITLES)


def _carry_commbuys(merged: list[dict], previous: list[dict]) -> None:
    prior = {}
    for record in previous:
        prior[_key(record)] = record
    for record in merged:
        old = prior.get(_key(record))
        if not old:
            continue
        if old.get("on_commbuys"):
            record["on_commbuys"] = old["on_commbuys"]
        if old.get("commbuys_url"):
            record["commbuys_url"] = old["commbuys_url"]


def _key(record: dict) -> tuple:
    from scraper.store import norm_bid_number

    number = norm_bid_number(record.get("bid_number")).casefold()
    if number:
        return ("num", (record.get("town") or "").casefold(), number)
    return ("url", canonical_url(record.get("url") or ""))


def _label_bids(bids: list[dict], town_regions: dict[str, str]) -> None:
    for bid in bids:
        if not bid.get("source"):
            bid["source"] = "town"
        if not bid.get("region"):
            bid["region"] = town_regions.get(bid.get("town") or "")
        if bid.get("source") in AGENCY_SOURCES:
            bid["sector"] = "agency"
        elif not bid.get("sector"):
            bid["sector"] = "town"
        bid["trade"] = cladding_trade(bid.get("title"), bid.get("category"))
        border = STATE_BY_REGION.get(bid.get("region") or "")
        if border:
            bid["state"] = border
        elif not bid.get("state"):
            bid["state"] = "Massachusetts"


def _central_register_status() -> dict:
    return {
        "status": "skipped",
        "last_success": None,
        "last_error": None,
        "message": CENTRAL_REGISTER_SKIPPED,
        "rows": 0,
    }


def _town_status(town_order, reports, bids, previous_status, now, town_regions) -> dict:
    towns = {}
    previous_towns = previous_status.get("towns") or {}
    seen = []
    for town in town_order:
        if town not in seen:
            seen.append(town)
    for town in seen:
        mine = [report for report in reports if report["town"] == town]
        failed = [report for report in mine if report["status"] == "error"]
        skipped = [report for report in mine if report["status"] == "skipped"]
        open_count = sum(
            1 for bid in bids if bid.get("town") == town and bid.get("status") == "open"
        )
        prior = previous_towns.get(town) or {}
        if failed:
            message = "; ".join(
                f"{report['type']}: {report['message']}" for report in failed
            )
            entry = {
                "status": "error",
                "last_success": prior.get("last_success"),
                "last_error": _iso(now),
                "message": message,
                "open_count": open_count,
            }
        elif skipped and not any(report["status"] == "ok" for report in mine):
            entry = {
                "status": "skipped",
                "last_success": prior.get("last_success"),
                "last_error": None,
                "message": "; ".join(report["message"] or "Skipped" for report in skipped),
                "open_count": open_count,
            }
        else:
            entry = {
                "status": "ok",
                "last_success": _iso(now),
                "last_error": None,
                "message": None,
                "open_count": open_count,
            }
        entry["region"] = town_regions.get(town)
        entry["sources"] = [
            {
                "type": report["type"],
                "url": report["url"],
                "status": report["status"],
                "count": report["count"],
                "message": report["message"],
            }
            for report in mine
        ]
        towns[town] = entry
    return towns


def _commbuys_status(previous_status: dict, now: datetime) -> dict:
    prior = previous_status.get("commbuys") or {}
    return {
        "status": "error",
        "last_success": prior.get("last_success"),
        "last_error": _iso(now),
        "message": "not checked",
        "rows": prior.get("rows"),
    }


def _print_summary(town_order, bids, status_doc, opened, elapsed: float) -> None:
    print("\nSource             open  upcoming  leads", flush=True)
    for source, label in (
        ("town", "Town page"),
        ("commbuys", "COMMBUYS"),
        ("central_register", "Central Register"),
        ("legal_notice", "Legal notices"),
        ("bid_express", "Bid Express"),
        ("massdevelopment", "MassDevelopment"),
        ("mbta", "MBTA"),
        ("msba", "MSBA"),
        ("massdot", "MassDOT"),
        ("dcr", "DCR"),
        ("mapc", "MAPC"),
        ("sam_gov", "SAM.gov"),
        ("boston_planning", "Boston Planning"),
        ("planning_board", "Planning board"),
    ):
        open_count = sum(
            1
            for bid in bids
            if bid.get("source") == source
            and bid.get("status") == "open"
            and (bid.get("kind") or "open") == "open"
        )
        upcoming_count = sum(
            1
            for bid in bids
            if bid.get("source") == source
            and bid.get("status") == "open"
            and bid.get("kind") == "upcoming"
        )
        lead_count = sum(
            1
            for bid in bids
            if bid.get("source") == source and bid.get("status") == "open" and bid.get("kind") == "lead"
        )
        print(f"{label:<18} {open_count:>4}  {upcoming_count:>8}  {lead_count:>5}", flush=True)
    print("\nRegion                 open", flush=True)
    regions = []
    for bid in bids:
        region = bid.get("region") or ""
        if region and region not in regions:
            regions.append(region)
    for region in regions:
        open_count = sum(
            1
            for bid in bids
            if bid.get("region") == region and bid.get("status") == "open" and (bid.get("kind") or "open") == "open"
        )
        print(f"{region:<22} {open_count:>4}", flush=True)
    print("\nSector                 open", flush=True)
    for sector in ("town", "school", "housing", "agency", "utility", "college", "hospital", "private", "federal"):
        open_count = sum(
            1
            for bid in bids
            if (bid.get("sector") or "town") == sector
            and bid.get("status") == "open"
            and (bid.get("kind") or "open") == "open"
        )
        print(f"{sector:<22} {open_count:>4}", flush=True)
    cladding = [
        bid
        for bid in bids
        if bid.get("trade") == "cladding" and bid.get("status") == "open"
    ]
    lead_rows = [bid for bid in bids if bid.get("kind") == "lead" and bid.get("status") == "open"]
    print(f"\nEarly leads                {len(lead_rows):>4}", flush=True)
    for bid in lead_rows[:8]:
        print(f"  {bid.get('source')}: {bid.get('town')}: {bid.get('title')}", flush=True)
    print(f"\nCladding tag               {len(cladding):>4}", flush=True)
    for bid in cladding[:8]:
        print(f"  {bid.get('town')}: {bid.get('title')}", flush=True)
    print("\nTown                           region            open  closed  status", flush=True)
    for town in town_order:
        info = (status_doc.get("towns") or {}).get(town) or {}
        open_count = sum(1 for bid in bids if bid["town"] == town and bid["status"] == "open")
        closed_count = sum(1 for bid in bids if bid["town"] == town and bid["status"] == "closed")
        region = ""
        for bid in bids:
            if bid.get("town") == town and bid.get("region"):
                region = bid["region"]
                break
        region = region or info.get("region") or ""
        state = info.get("status") or "n/a"
        message = info.get("message") or ""
        print(f"{town:<30} {region:<16} {open_count:>4}  {closed_count:>6}  {state}  {message}", flush=True)
    dupes = _duplicate_open(bids)
    print(f"\nDuplicate open bids: {len(dupes)}", flush=True)
    for kind, key in dupes:
        print(f"  {kind}: {key}", flush=True)
    print(f"New open bids: {len(opened)}", flush=True)
    for bid in opened:
        due = bid.get("due_date") or "no due date"
        print(f"  {bid['town']}: {bid['title']} ({due})", flush=True)
    print(f"Runtime: {elapsed:.0f}s", flush=True)
    register = status_doc.get("central_register") or {}
    if register.get("status") == "skipped":
        print(f"Central Register: {register.get('message')}", flush=True)


def _duplicate_open(bids: list[dict]) -> list[tuple[str, str]]:
    seen_url: dict[str, int] = {}
    seen_num: dict[tuple[str, str], int] = {}
    found: list[tuple[str, str]] = []
    for bid in bids:
        if bid.get("status") != "open":
            continue
        url = canonical_url(bid.get("url") or "")
        if url:
            seen_url[url] = seen_url.get(url, 0) + 1
            if seen_url[url] == 2:
                found.append(("url", url))
        number = norm_bid_number(bid.get("bid_number")).casefold()
        if number:
            key = ((bid.get("town") or "").casefold(), number)
            seen_num[key] = seen_num.get(key, 0) + 1
            if seen_num[key] == 2:
                found.append(("bid_number", f"{key[0]} {key[1]}"))
    return found


def _iso(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=EASTERN)
    return now.astimezone(EASTERN).isoformat(timespec="seconds")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"scrape failed: {exc}", flush=True)
        traceback.print_exc()
        raise SystemExit(1)
