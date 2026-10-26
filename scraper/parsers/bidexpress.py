"""Bid Express agency home pages.

DCAMM and Massport publish the same HTML table: solicitation number,
description, and a deadline in UTC. Only the Upcoming Solicitations table
is read. Closed solicitations stay on the page and are left there.
The solicitation link is public; the site's login pages are not requested.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin

from scraper.dates import EASTERN

_UPCOMING = re.compile(r"<h3>\s*Upcoming Solicitations\s*</h3>", re.IGNORECASE)
_TABLE = re.compile(r"<table\b[\s\S]*?</table>", re.IGNORECASE)
_ROW = re.compile(r'<tr id="Solicitation_(\d+)"[\s\S]*?</tr>', re.IGNORECASE)
_LINK = re.compile(r'<a href="([^"]+)">([^<]*)</a>', re.IGNORECASE)
_DESC = re.compile(r'data-popup-content="([^"]*)"', re.IGNORECASE)
_DEADLINE = re.compile(
    r'id="Solicitation_\d+_Deadline">\s*([^<]+)',
    re.IGNORECASE,
)
_UTC = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})\s*([AaPp])\.?[Mm]\.?\s*UTC"
)


class ParseError(Exception):
    pass


def parse_bid_express(html: str, page_url: str, agency: str) -> list[dict]:
    start = _UPCOMING.search(html)
    if not start:
        raise ParseError(f"{agency} Bid Express page had no upcoming solicitations")
    table = _TABLE.search(html, start.end())
    if not table:
        raise ParseError(f"{agency} Bid Express page had no upcoming table")
    chunk = table.group(0)
    items = []
    seen = set()
    for row in _ROW.finditer(chunk):
        solicitation_id = row.group(1)
        if solicitation_id in seen:
            continue
        seen.add(solicitation_id)
        block = row.group(0)
        link = _LINK.search(block)
        if not link:
            continue
        number = _clean(link.group(2))
        desc = _DESC.search(block)
        title = _clean(unescape(desc.group(1))) if desc else ""
        deadline = _DEADLINE.search(block)
        items.append(
            {
                "town": agency,
                "region": "Statewide agency",
                "source": "bid_express",
                "kind": "open",
                "title": title or number or f"{agency} solicitation {solicitation_id}",
                "bid_number": number or solicitation_id,
                "category": agency,
                "posted_date": None,
                "due": _utc_to_eastern(deadline.group(1) if deadline else ""),
                "url": urljoin(page_url, link.group(1)),
                "source_url": page_url,
            }
        )
    return items


def _utc_to_eastern(text: str) -> str | None:
    match = _UTC.search(text or "")
    if not match:
        return None
    month, day, year, hour, minute, ampm = match.groups()
    hh = int(hour)
    if ampm.lower() == "p" and hh != 12:
        hh += 12
    if ampm.lower() == "a" and hh == 12:
        hh = 0
    try:
        moment = datetime(
            int(year), int(month), int(day), hh, int(minute), tzinfo=timezone.utc
        )
    except ValueError:
        return None
    return moment.astimezone(EASTERN).isoformat(timespec="seconds")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value or "").replace("\xa0", " ")).strip()
