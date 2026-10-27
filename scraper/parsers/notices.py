"""A public HTML page whose bids are document links or dated headings.

Used for town pages that are not a CivicPlus Bids module, and for school
and housing-authority pages. Closed, addenda, forms, and budget notices
are left out. An empty list is a successful read of a page with nothing open.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

from scraper.dates import parse_when

_MARKERS = (
    'class="sk-page-content',
    'id="fspagecontent"',
    'class="entry-content',
    "<main",
    'id="content"',
    'class="content',
)
_ANCHOR = re.compile(r"(?is)<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>")
_TAG = re.compile(r"(?is)<[^>]+>")
_DOCUMENT = re.compile(
    r"(?i)(\.pdf|\.docx?|\.xlsx?)(?:$|[?#])"
    r"|resource-manager/view/"
    r"|cloudfront\.net/"
    r"|drive\.google\.com/(file|drive)/"
    r"|wp-content/uploads/"
    r"|linkclick\.aspx"
    r"|/portals/\d+/"
)
_KEEP = re.compile(
    r"(?i)\b(bids?|rfp|rfq|ifb|invitation|procurement|solicitation)\b"
)
_SKIP = re.compile(
    r"(?i)\b("
    r"addend(um|a)|cancell?ed|bid results|results|budget|public hearing|"
    r"request bid document|no current bids|insurance|affidavit|"
    r"instructions to offerors|hud[-\s]?\d"
    r")\b"
)
_CLOSED_BEFORE = re.compile(r"(?i)\bclosed\b")
_OPEN_BEFORE = re.compile(r"(?i)\bopen\b")
_NUMBER = re.compile(r"(?i)\b(?:bid|ifb|rfp|rfq)\s*#?\s*(\d{2,})\b")
_BID_OPENING = re.compile(
    r"(?is)<strong>(.*?)</strong>\s*(?:<[^>]+>\s*)*"
    r"(?:<strong>)?\s*bid opening\s+([^<]{6,80})"
)


class ParseError(Exception):
    pass


def parse_notice_page(html: str, page_url: str) -> list[dict]:
    content = _slice(html)
    if content is None:
        raise ParseError("notice page had no content area")
    items: list[dict] = []
    seen: set[str] = set()
    for match in _ANCHOR.finditer(content):
        href, raw = match.group(1), match.group(2)
        title = _text(raw)
        target = unescape(href).strip()
        window = _text(content[max(0, match.start() - 280) : match.start()])
        if not title or not target or target.startswith(("#", "mailto:", "javascript:")):
            continue
        if "heygov.com" in target.lower() or "/forms/" in target.lower():
            continue
        blob = f"{title} {target}"
        if not _DOCUMENT.search(target):
            continue
        if not _KEEP.search(blob):
            continue
        if _SKIP.search(title) or _SKIP.search(target):
            continue
        if _CLOSED_BEFORE.search(window) and not _OPEN_BEFORE.search(window[-40:]):
            continue
        url = urljoin(page_url, target)
        if url in seen:
            continue
        seen.add(url)
        number = _NUMBER.search(title)
        following = content[content.find(raw) : content.find(raw) + 400] if raw in content else title
        items.append(
            {
                "title": title,
                "url": url,
                "bid_number": number.group(1) if number else None,
                "posted_date": None,
                "due": parse_when(f"{title} {_text(following)}"),
                "category": None,
            }
        )
    for title_raw, when_raw in _BID_OPENING.findall(content):
        title = _text(title_raw)
        when = _text(when_raw)
        if not title or _SKIP.search(title):
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
        url = f"{page_url.split('#', 1)[0]}#{slug}"
        if url in seen or any(item["title"].casefold() == title.casefold() for item in items):
            continue
        seen.add(url)
        items.append(
            {
                "title": title,
                "url": url,
                "bid_number": None,
                "posted_date": None,
                "due": parse_when(when.replace("@", " at "))
                or parse_when(f"{title} {when}".replace("@", " at ")),
                "category": None,
            }
        )
    return items


def _slice(html: str) -> str | None:
    lowered = html.lower()
    for marker in _MARKERS:
        index = lowered.find(marker)
        if index >= 0:
            return html[index : index + 80_000]
    return None


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG.sub(" ", value))).strip()
