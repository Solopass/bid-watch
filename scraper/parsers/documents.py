"""A plain CivicPlus content page whose bids are headings with PDF links.

Holliston lists each posting as a heading. The first document link is the
bid; later links in the same heading are addenda or Q&A. The due date is
free text in the heading. Closed items stay on the page, so the due date
is what marks them closed.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

from scraper.dates import parse_when

_VIEW = re.compile(
    r'class="fr-view"[^>]*>([\s\S]*?)</div>\s*<script',
    re.IGNORECASE,
)
_HEADING = re.compile(r"<h[1-4][^>]*>(.*?)</h[1-4]>", re.IGNORECASE | re.DOTALL)
_ANCHOR = re.compile(
    r'<a[^>]+href="([^"]*DocumentCenter/View/\d+[^"]*)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_DUE = re.compile(
    r"(?i)(?:quotes\s+due|bids\s+due|due)(?:\s+by)?\s+"
    r"([A-Za-z]+\s+\d{1,2},\s+\d{4})"
    r"(?:\s+(?:at|by)\s+(\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?))?"
)
_SKIP_TITLE = re.compile(r"(?i)^(addendum|q\s*&\s*a|q&a|question)")
_TAG = re.compile(r"<[^>]+>")


class ParseError(Exception):
    pass


def parse_document_page(html: str, page_url: str) -> list[dict]:
    view = _VIEW.search(html)
    if not view:
        if "fr-view" not in html:
            raise ParseError("document-page content area was not on the page")
        start = html.lower().find('class="fr-view"')
        view_html = html[start:start + 20000]
    else:
        view_html = view.group(1)
    items = []
    for heading in _HEADING.findall(view_html):
        plain = _text(heading)
        if not plain or "closed bid" in plain.lower():
            continue
        links = _ANCHOR.findall(heading)
        primary = None
        for href, raw in links:
            title = _text(raw)
            if not title or _SKIP_TITLE.search(title):
                continue
            primary = (href, title)
            break
        if not primary:
            continue
        due_match = _DUE.search(plain)
        spoken = None
        if due_match:
            spoken = due_match.group(1)
            if due_match.group(2):
                spoken = f"{spoken} at {due_match.group(2)}"
        items.append(
            {
                "title": primary[1],
                "url": urljoin(page_url, unescape(primary[0])),
                "bid_number": None,
                "posted_date": None,
                "due": parse_when(spoken) if spoken else parse_when(plain),
                "category": None,
            }
        )
    if not items and "DocumentCenter/View/" in view_html:
        raise ParseError("document page linked files but no bid headings could be read")
    return items


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG.sub(" ", value))).strip()
