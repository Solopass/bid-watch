"""CivicPlus Bids.aspx list pages.

The open list is one HTML page. Each row carries a title, optional bid
number, status, and closing text. Award leftovers often stay "Open" with
"Upon Contract"; callers decide whether to skip those or open the detail
page. Detail pages publish the real closing time in the social description.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

from scraper.dates import as_date, parse_when

_ROW = re.compile(
    r'<div class="listItemsRow bid(?:\s+alt)?"[^>]*>(.*?)'
    r'(?=<div class="(?:bidsHeader|listItemsRow bid)|$)',
    re.IGNORECASE | re.DOTALL,
)
_HEADER = re.compile(
    r'<div class="bidsHeader[^"]*"[^>]*>\s*<span>([^<]*)</span>',
    re.IGNORECASE | re.DOTALL,
)
_ANCHOR = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_BID_NO = re.compile(r"Bid No\.\s*</strong>\s*([^<]+)", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


class ParseError(Exception):
    pass


def parse_civicplus(html: str, page_url: str) -> list[dict]:
    if "bidItems" not in html and "listItemsRow bid" not in html:
        raise ParseError("CivicPlus bid list markup was not on the page")
    if 'class="listItemsRow bid' in html and not _ROW.search(html):
        raise ParseError("CivicPlus rows were present but none could be read")

    category = None
    items: list[dict] = []
    cursor = 0
    markers = []
    for match in _HEADER.finditer(html):
        markers.append((match.start(), "header", match))
    for match in _ROW.finditer(html):
        markers.append((match.start(), "row", match))
    for _, kind, match in sorted(markers, key=lambda item: item[0]):
        if kind == "header":
            category = _text(match.group(1)) or category
            continue
        item = _row(match.group(1), page_url, category)
        if item:
            items.append(item)
        cursor = match.end()
    del cursor
    if not items and "no open bid" not in html.lower() and "bidItems" not in html:
        raise ParseError("CivicPlus page had no bids and no empty-state message")
    return items


def _row(chunk: str, page_url: str, category: str | None) -> dict | None:
    title = None
    href = None
    for link, raw_text in _ANCHOR.findall(chunk):
        text = _text(raw_text)
        if not text or text.lower().startswith("read on"):
            continue
        title = text
        href = unescape(link)
        break
    if not title or not href:
        return None
    number = _BID_NO.search(chunk)
    bid_number = _text(number.group(1)) if number else None
    status, closes = _status(chunk)
    return {
        "title": title,
        "url": urljoin(page_url, href),
        "bid_number": bid_number or None,
        "posted_date": None,
        "due": parse_when(closes),
        "close_raw": closes,
        "status_raw": status,
        "category": category,
    }


def _status(chunk: str) -> tuple[str, str]:
    match = re.search(r'class="bidStatus"[^>]*>(.*)', chunk, re.IGNORECASE | re.DOTALL)
    if not match:
        return "", ""
    divs = re.findall(r"<div>(.*?)</div>", match.group(1), re.IGNORECASE | re.DOTALL)
    target = divs[1] if len(divs) > 1 else match.group(1)
    spans = [_text(span) for span in re.findall(r"<span[^>]*>(.*?)</span>", target, re.DOTALL)]
    spans = [span for span in spans if span and not span.endswith(":")]
    if len(spans) >= 2:
        return spans[0], spans[1]
    if len(spans) == 1:
        return spans[0], ""
    return "", ""


def close_kind(item: dict) -> str:
    raw = item.get("close_raw") or ""
    if item.get("due"):
        return "dated"
    if re.search(r"upon\s+contract|until\s+contract", raw, re.IGNORECASE):
        return "contract"
    if raw.strip():
        return "other"
    return "blank"


def enrich_civicplus_detail(html: str) -> dict:
    """Pull publication date, closing date, and bid number from a detail page."""
    description = ""
    meta = re.search(
        r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"',
        html,
        re.IGNORECASE,
    )
    if not meta:
        meta = re.search(
            r'<meta[^>]+content="([^"]*)"[^>]+property="og:description"',
            html,
            re.IGNORECASE,
        )
    if meta:
        description = unescape(meta.group(1))
    visible = _text(re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html))
    closing = ""
    published = ""
    closing_match = re.search(r"Closing Date/Time:\s*([^;\"<]+)", description, re.IGNORECASE)
    if closing_match:
        closing = closing_match.group(1).strip()
    published_match = re.search(
        r"Bid Publication Date/Time:\s*([^;\"<]+)", description, re.IGNORECASE
    )
    if published_match:
        published = published_match.group(1).strip()
    due = parse_when(closing)
    if due is None:
        body = re.search(
            r"(?i)(?:bids?\s+must\s+be\s+received\s+by|sealed\s+bids?\s+will\s+be\s+received\s+until|"
            r"received\s+by|closing date/time:)\s*"
            r"(?:[A-Za-z]+,\s*)?(.+?)(?:\.|<|$)",
            visible,
        )
        if body:
            due = parse_when(body.group(1))
    number = None
    number_match = re.search(
        r"(?i)\b(?:bid|ifb|rfp|rfq)\s*(?:no\.?|number|#)\s*[:.]?\s*([A-Z0-9][A-Z0-9./-]{1,40})",
        visible,
    )
    if number_match:
        number = number_match.group(1).strip(" .")
    return {
        "due": due,
        "posted_date": as_date(published),
        "bid_number": number,
    }


def _text(value: str) -> str:
    value = unescape(_TAG.sub(" ", value))
    return re.sub(r"\s+", " ", value).strip()
