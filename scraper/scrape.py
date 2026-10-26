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
from scraper.parsers.documents import ParseError as DocParseError
from scraper.parsers.documents import parse_document_page
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

PARSE_ERRORS = (CivicParseError, DocParseError, WpParseError, BidNetParseError)
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

    for source in config["sources"]:
        report = {
            "town": source["town"],
            "type": source["type"],
            "url": canonical_url(source["url"]),
            "status": "ok",
            "count": 0,
            "message": None,
        }
        try:
            found = scrape_source(source, fetcher, known)
        except (FetchError, RobotsDisallowed, *PARSE_ERRORS) as exc:
            report["status"] = "error"
            report["message"] = str(exc)
            print(f"ERROR {source['town']} ({source['type']}): {exc}", flush=True)
        except Exception as exc:  # a parser bug should not sink the other towns
            report["status"] = "error"
            report["message"] = f"{type(exc).__name__}: {exc}"
            print(f"ERROR {source['town']} ({source['type']}): {exc}", flush=True)
            traceback.print_exc()
        else:
            report["count"] = len(found)
            successful.add(canonical_url(source["url"]))
            fresh.extend(found)
            print(f"OK {source['town']} ({source['type']}): {len(found)} listing(s)", flush=True)
        source_reports.append(report)

    commbuys_rows = None
    commbuys_report = _commbuys_status(previous_status, now)
    try:
        commbuys_rows = fetch_open_solicitations(fetcher)
    except (CommBuysError, FetchError, RobotsDisallowed) as exc:
        commbuys_report["status"] = "error"
        commbuys_report["message"] = str(exc)
        commbuys_report["last_success"] = (previous_status.get("commbuys") or {}).get("last_success")
        commbuys_report["last_error"] = _iso(now)
        print(f"COMMBUYS check failed ({exc}); flags left unknown where unset", flush=True)
    except Exception as exc:
        commbuys_report["status"] = "error"
        commbuys_report["message"] = f"{type(exc).__name__}: {exc}"
        commbuys_report["last_success"] = (previous_status.get("commbuys") or {}).get("last_success")
        commbuys_report["last_error"] = _iso(now)
        print(f"COMMBUYS check failed ({exc}); flags left unknown where unset", flush=True)
        traceback.print_exc()
    else:
        commbuys_report = {
            "status": "ok",
            "last_success": _iso(now),
            "last_error": None,
            "message": f"{len(commbuys_rows)} open solicitations",
            "rows": len(commbuys_rows),
        }
        print(f"OK COMMBUYS: {len(commbuys_rows)} open solicitations", flush=True)

    # Merge town pages first. COMMBUYS rows that match a town bid, open or
    # closed, update that bid instead of being copied. Unmatched covered
    # rows are then merged with the COMMBUYS search marked successful, so a
    # covered solicitation that leaves the CSV is closed and a failed export
    # leaves the previous COMMBUYS rows in place.
    merged = merge_bids(previous_doc.get("bids", []), fresh, successful, now)
    added = 0
    if commbuys_rows is not None:
        extras = covered_rows(merged, commbuys_rows, town_regions)
        added = len(extras)
        fresh.extend(extras)
        successful.add(canonical_url(SEARCH_URL))
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
    else:
        raise FetchError(f"unknown page type {kind!r}")
    source_url = canonical_url(page_url)
    for item in listings:
        item["town"] = source["town"]
        item["region"] = source.get("region")
        item["source"] = "town"
        item["source_url"] = source_url
        item["url"] = canonical_url(item["url"])
        item.pop("close_raw", None)
        item.pop("status_raw", None)
    return listings


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
        failed = [report for report in mine if report["status"] != "ok"]
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
    print("\nSource             open", flush=True)
    for source, label in (
        ("town", "Town page"),
        ("commbuys", "COMMBUYS"),
        ("central_register", "Central Register"),
    ):
        count = sum(1 for bid in bids if bid.get("source") == source and bid.get("status") == "open")
        print(f"{label:<18} {count:>4}", flush=True)
    print("\nTown                  region           open  closed  status", flush=True)
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
        print(f"{town:<21} {region:<16} {open_count:>4}  {closed_count:>6}  {state}  {message}", flush=True)
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
