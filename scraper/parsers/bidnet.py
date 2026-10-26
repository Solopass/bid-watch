"""BidNet Direct agency pages.

The open-solicitation table is server-rendered. Detail pages ask for a
free registration, so this parser stays on the public list. An empty-state
sentence is baked into the table even when rows exist; a page is empty
only when it has no solicitation links.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

from scraper.dates import as_date, parse_when

_ROW = re.compile(
    r'<tr[^>]*class="mets-table-row(?:\s[^"]*)?"[^>]*>(.*?)</tr>',
    re.IGNORECASE | re.DOTALL,
)
_NUM = re.compile(r'class="sol-num"[^>]*>\s*(.*?)\s*</div>', re.IGNORECASE | re.DOTALL)
_ANCHOR = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_PUBLISHED = re.compile(
    r'class="sol-publication-date"[\s\S]*?class="date-value"[^>]*>\s*([^<]+)',
    re.IGNORECASE,
)
_CLOSING = re.compile(
    r'class="sol-closing-date[\s\S]*?class="date-value"[^>]*>\s*([^<]+)',
    re.IGNORECASE,
)
_TAG = re.compile(r"<[^>]+>")


class ParseError(Exception):
    pass


def parse_bidnet(html: str, page_url: str) -> list[dict]:
    items = []
    for chunk in _ROW.findall(html):
        link = _solicitation_link(chunk)
        if not link:
            continue
        href, title = link
        number = _text(_NUM.search(chunk).group(1)) if _NUM.search(chunk) else ""
        if number and title.lower().startswith(number.lower()):
            trimmed = title[len(number):].lstrip(" -:").strip()
            if trimmed:
                title = trimmed
        published = _PUBLISHED.search(chunk)
        closing = _CLOSING.search(chunk)
        items.append(
            {
                "title": title,
                "url": urljoin(page_url, href),
                "bid_number": number or None,
                "posted_date": as_date(_text(published.group(1))) if published else None,
                "due": parse_when(_text(closing.group(1))) if closing else None,
                "category": None,
            }
        )
    if not items and "solicitation-link" in html:
        raise ParseError("BidNet page linked solicitations but no rows could be read")
    if not items and "mets-table" not in html and "no open bid" not in html.lower():
        raise ParseError("BidNet solicitation list was not on the page")
    return items


def _solicitation_link(chunk: str):
    for attrs, inner in _ANCHOR.findall(chunk):
        if "solicitation-link" not in attrs:
            continue
        href = re.search(r'href="([^"]+)"', attrs, re.IGNORECASE)
        if not href:
            continue
        return unescape(href.group(1)), _text(inner)
    return None


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG.sub(" ", value))).strip()
