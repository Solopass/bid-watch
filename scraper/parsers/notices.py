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
    "elementor-widget-container",
)
_ANCHOR = re.compile(r"(?is)<a\b([^>]*)>(.*?)</a>")
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
    r"(?i)(?:\b(bids?|rfp|rfq|ifb|invitation|procurement|solicitation|advertisement)\b"
    r"|(?:^|[-_/])ad[-_])"
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
class ParseError(Exception):
    pass


def parse_notice_page(html: str, page_url: str) -> list[dict]:
    content = _slice(html)
    if content is None:
        raise ParseError("notice page had no content area")
    items: list[dict] = []
    seen: set[str] = set()
    for match in _ANCHOR.finditer(content):
        attrs, raw = match.group(1), match.group(2)
        href_match = re.search(r"""(?is)\bhref\s*=\s*(?:"([^"]+)"|'([^']+)')""", attrs)
        if not href_match:
            continue
        target = unescape((href_match.group(1) or href_match.group(2) or "")).strip()
        label_match = re.search(r"""(?is)\baria-label\s*=\s*(?:"([^"]+)"|'([^']+)')""", attrs)
        label = _text(label_match.group(1) or label_match.group(2) or "") if label_match else ""
        title = _title(raw, target)
        if label and _generic(title):
            title = re.sub(r"(?i)^learn more about\s+", "", label).strip() or title
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
    for opening in re.finditer(r"(?i)bid opening\s+([^<]{6,80})", content):
        when = _text(opening.group(1))
        before = content[max(0, opening.start() - 700) : opening.start()]
        titles = [_text(raw) for raw in re.findall(r"(?is)<strong>(.*?)</strong>", before)]
        titles = [title for title in titles if title and not _SKIP.search(title)]
        if not titles:
            continue
        title = titles[-1]
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
    """Use the content region that actually holds the documents.

    The first ``content`` class on a page is often a stylesheet or a footer.
    """
    lowered = html.lower()
    best = None
    best_score = -1
    for marker in _MARKERS:
        start = 0
        needle = marker.lower()
        while True:
            index = lowered.find(needle, start)
            if index < 0:
                break
            chunk = html[index : index + 80_000]
            score = len(_DOCUMENT.findall(chunk))
            if _KEEP.search(chunk):
                score += 3
            if score > best_score:
                best = chunk
                best_score = score
            start = index + len(needle)
    return best


def _generic(title: str) -> bool:
    cleaned = title.lower().strip(" ->")
    return cleaned in {"more info", "click here", "here", "download", "pdf", "view", "read more"}


def _title(raw: str, target: str) -> str:
    from urllib.parse import unquote

    title = _text(raw)
    words = re.findall(r"[A-Za-z]{3,}", title)
    if len(words) >= 2 and not title.lower().startswith("m181"):
        return title
    name = unquote(target.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1])
    name = re.sub(r"(?i)\.(pdf|docx?|xlsx?)$", "", name)
    name = re.sub(r"[-_]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or title


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG.sub(" ", value))).strip()
