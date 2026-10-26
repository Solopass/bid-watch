"""MBTA bidding pages.

Current solicitations are lists of links. A construction row carries the
contract number in an HTML comment. Due dates are not on the list; the
caller may open a detail page when that page is public.

Future solicitations are one HTML table (title, contract, advertise date,
bid date, work type, estimate). Those rows are upcoming projects, not open
bids. Every MBTA row is kept and tagged as a statewide agency, including
work in Boston.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

from scraper.dates import parse_when

_TAG = re.compile(r"<[^>]+>")
_SECTION = re.compile(r"<h4[^>]*>([\s\S]*?)</h4>([\s\S]*?)(?=<h4\b|</div>\s*</div>)", re.IGNORECASE)
_COMMENT_ROW = re.compile(
    r"<!--\s*contract_nbr:\s*([^>]*?)\s*-->\s*<li[^>]*>\s*<a[^>]+href=\"([^\"]+)\"[^>]*>([\s\S]*?)</a>",
    re.IGNORECASE,
)
_LINK_ROW = re.compile(
    r"<li[^>]*>\s*<a[^>]+href=\"([^\"]+)\"[^>]*>([\s\S]*?)</a>",
    re.IGNORECASE,
)
_TABLE = re.compile(r"<table[\s\S]*?</table>", re.IGNORECASE)
_ROW = re.compile(r"<tr[^>]*>([\s\S]*?)</tr>", re.IGNORECASE)
_CELL = re.compile(r"<td[^>]*>([\s\S]*?)</td>", re.IGNORECASE)
_RECEIVED = re.compile(
    r"(?i)(?:bids must be received by|proposals must be received by|due date)\s*:?\s*([^<]{0,80})"
)


class ParseError(Exception):
    pass


def parse_current_solicitations(html: str, page_url: str) -> list[dict]:
    if "solicitation" not in html.lower():
        raise ParseError("MBTA current solicitations page did not contain a solicitation list")
    items = []
    seen = set()
    for heading, body in _SECTION.findall(html):
        label = _text(heading)
        if not label.lower().startswith("current"):
            continue
        if "awarded" in label.lower() or "response" in label.lower():
            continue
        for contract, href, title_html in _COMMENT_ROW.findall(body):
            _add(items, seen, page_url, _text(title_html), href, _number(contract), label, "open")
        if _COMMENT_ROW.search(body):
            continue
        for href, title_html in _LINK_ROW.findall(body):
            if not re.search(r"cmarid=|dbid=|cbid=|solicitation", href, re.IGNORECASE):
                continue
            _add(items, seen, page_url, _text(title_html), href, None, label, "open")
    return items


def parse_future_solicitations(html: str, page_url: str) -> list[dict]:
    tables = _TABLE.findall(html)
    if not tables:
        raise ParseError("MBTA future solicitations page had no table")
    items = []
    seen = set()
    for table in tables:
        if "bid date" not in table.lower():
            continue
        for row in _ROW.findall(table):
            cells = [_text(cell) for cell in _CELL.findall(row)]
            if len(cells) < 6 or not cells[0]:
                continue
            title, number, advertised, bid_date, work, estimate = cells[:6]
            number = _number(number)
            slug = _slug(number or title)
            category = " · ".join(part for part in (work, estimate) if part)
            _add(
                items,
                seen,
                page_url,
                title,
                f"#{slug}",
                number,
                category or "Upcoming",
                "upcoming",
                posted=parse_when(advertised),
                due=parse_when(bid_date),
            )
    return items


def due_from_detail(html: str) -> str | None:
    """Due date from a public construction-bid detail page, if the page states one."""
    match = _RECEIVED.search(html or "")
    if not match:
        return None
    spoken = re.sub(r"(?i)\bat\b", " ", match.group(1))
    return parse_when(spoken)


def _add(items, seen, page_url, title, href, number, category, kind, posted=None, due=None):
    if not title:
        return
    url = urljoin(page_url, unescape(href))
    if url in seen:
        return
    seen.add(url)
    items.append(
        {
            "town": "MBTA",
            "region": "Statewide agency",
            "source": "mbta",
            "kind": kind,
            "title": title,
            "bid_number": number,
            "category": category,
            "posted_date": posted,
            "due": due,
            "url": url,
            "source_url": page_url,
        }
    )


def _number(value: str | None) -> str | None:
    cleaned = _text(value or "")
    if not cleaned or cleaned.casefold() in {"tbd", "n/a", "na", "none"}:
        return None
    return cleaned


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "project"


def _text(value: str) -> str:
    cleaned = _TAG.sub(" ", value or "")
    return re.sub(r"\s+", " ", unescape(cleaned).replace("\xa0", " ")).strip()
