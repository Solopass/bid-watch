"""Merge a scrape into the bid ledger.

Records are deduped by canonical URL and, when both sides have one, by
town plus bid number. ``first_seen`` is kept. A bid is closed when its due
date has passed or its listing source succeeded and no longer shows it.
A failed source does not close the bids it previously found.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from scraper.dates import EASTERN, is_past, today_eastern

FIELDS = (
    "town",
    "title",
    "bid_number",
    "category",
    "posted_date",
    "due_date",
    "url",
    "source_url",
    "on_commbuys",
    "commbuys_url",
    "status",
    "first_seen",
    "last_seen",
    "closed_on",
)


def canonical_url(url: str) -> str:
    parts = urlsplit((url or "").strip())
    scheme = parts.scheme.lower() or "https"
    if scheme not in ("http", "https"):
        scheme = "https"
    host = parts.netloc.lower()
    path = parts.path or "/"
    if path.lower().endswith("/bids.aspx"):
        prefix, _, _ = path.rpartition("/")
        path = f"{prefix}/bids.aspx"
    query_pairs = []
    bids_page = path.endswith("/bids.aspx")
    for key, value in parse_qsl(parts.query, keep_blank_values=False):
        lowered = key.lower()
        if lowered in {"jsessionid", "sessionid"} or lowered.startswith("utm_"):
            continue
        if bids_page and lowered != "bidid":
            continue
        query_pairs.append(("bidID" if lowered == "bidid" else key, value))
    query_pairs.sort()
    return urlunsplit((scheme, host, path, urlencode(query_pairs), ""))


def norm_bid_number(value: str | None) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip(" .")
    return cleaned


def load_document(path: Path) -> dict:
    if not path.exists():
        return {"updated": None, "bids": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("bids", [])
    return data


def load_status(path: Path) -> dict:
    if not path.exists():
        return {"updated": None, "towns": {}, "commbuys": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("towns", {})
    data.setdefault("commbuys", {})
    return data


def merge_bids(
    previous: list[dict],
    fresh: list[dict],
    successful_sources: set[str],
    now: datetime | None = None,
) -> list[dict]:
    now = now or datetime.now(EASTERN)
    if now.tzinfo is None:
        now = now.replace(tzinfo=EASTERN)
    today = today_eastern(now)
    fresh = _dedupe_fresh(fresh)

    by_url: dict[str, dict] = {}
    by_number: dict[tuple[str, str], dict] = {}
    for record in previous:
        url_key = canonical_url(record.get("url") or "")
        if url_key:
            by_url[url_key] = record
        number = norm_bid_number(record.get("bid_number")).casefold()
        if number:
            by_number[(record.get("town", "").casefold(), number)] = record

    used: set[int] = set()
    merged: list[dict] = []
    for item in fresh:
        url_key = canonical_url(item.get("url") or "")
        number = norm_bid_number(item.get("bid_number")).casefold()
        prev = by_url.get(url_key)
        if prev is None and number:
            prev = by_number.get((item.get("town", "").casefold(), number))
        if prev is not None and id(prev) in used:
            prev = None
        record = _combine(prev, item, today, now)
        if prev is not None:
            used.add(id(prev))
        merged.append(record)

    for record in previous:
        if id(record) in used:
            continue
        source = canonical_url(record.get("source_url") or "")
        if source and source in successful_sources:
            kept = dict(record)
            if kept.get("status") != "closed":
                kept["status"] = "closed"
                kept["closed_on"] = kept.get("closed_on") or today
            merged.append(_ordered(kept))
        else:
            merged.append(_ordered(record))
    return merged


def _dedupe_fresh(fresh: list[dict]) -> list[dict]:
    by_url: dict[str, dict] = {}
    by_number: dict[tuple[str, str], dict] = {}
    ordered: list[dict] = []
    for item in fresh:
        url_key = canonical_url(item.get("url") or "")
        number = norm_bid_number(item.get("bid_number")).casefold()
        town = (item.get("town") or "").casefold()
        existing = by_url.get(url_key) if url_key else None
        if existing is None and number:
            existing = by_number.get((town, number))
        if existing is None:
            ordered.append(item)
            if url_key:
                by_url[url_key] = item
            if number:
                by_number[(town, number)] = item
            continue
        for field in ("title", "bid_number", "due", "due_date", "posted_date", "category"):
            if not existing.get(field) and item.get(field):
                existing[field] = item[field]
    return ordered


def _combine(prev: dict | None, item: dict, today: str, now: datetime) -> dict:
    due = item.get("due") or item.get("due_date")
    past = is_past(due, now) if due else False
    if past:
        status = "closed"
        closed_on = (prev or {}).get("closed_on") or today
    else:
        status = "open"
        closed_on = None
    base = dict(prev or {})
    base.update(
        {
            "town": item.get("town") or base.get("town"),
            "title": item.get("title") or base.get("title"),
            "bid_number": norm_bid_number(item.get("bid_number")) or base.get("bid_number") or None,
            "category": item.get("category") if item.get("category") is not None else base.get("category"),
            "posted_date": item.get("posted_date") or base.get("posted_date"),
            "due_date": due or base.get("due_date"),
            "url": canonical_url(item.get("url") or base.get("url") or ""),
            "source_url": canonical_url(item.get("source_url") or base.get("source_url") or ""),
            "on_commbuys": base.get("on_commbuys") or "unknown",
            "commbuys_url": base.get("commbuys_url"),
            "status": status,
            "first_seen": (prev or {}).get("first_seen") or today,
            "last_seen": today,
            "closed_on": closed_on,
        }
    )
    if not base.get("bid_number"):
        base["bid_number"] = None
    return _ordered(base)


def _ordered(record: dict) -> dict:
    ordered = {}
    for field in FIELDS:
        value = record.get(field)
        if field == "bid_number":
            value = norm_bid_number(value) or None
        ordered[field] = value
    return ordered


def sort_bids(bids: list[dict], town_order: list[str]) -> list[dict]:
    order = {town: index for index, town in enumerate(town_order)}

    def key(bid: dict):
        due = bid.get("due_date") or "9999-99-99"
        return (
            order.get(bid.get("town"), 999),
            0 if bid.get("status") == "open" else 1,
            due,
            (bid.get("title") or "").lower(),
        )

    return sorted(bids, key=key)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def new_open_bids(previous: list[dict], current: list[dict]) -> list[dict]:
    """Open bids that were not already open in the previous snapshot."""
    previous_open = set()
    for record in previous:
        if record.get("status") == "open":
            previous_open.add(_identity(record))
    found = []
    for record in current:
        if record.get("status") == "open" and _identity(record) not in previous_open:
            found.append(record)
    return found


def _identity(record: dict) -> tuple:
    number = norm_bid_number(record.get("bid_number")).casefold()
    if number:
        return ("num", record.get("town", "").casefold(), number)
    return ("url", canonical_url(record.get("url") or ""))
