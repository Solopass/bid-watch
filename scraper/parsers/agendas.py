"""Planning-board agenda items that look like early construction leads.

CivicPlus Agenda Center pages often list nothing until a search runs.
When the public HTML has no agenda file link, the town is skipped.
A human-check page is skipped. At most one planning-board PDF is read
per town, and only a small number of those PDFs per run.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

from scraper.dates import parse_when

MAX_AGENDA_PDFS = 8

_BOARD = re.compile(r"(?i)planning board|planning commission|planning committee")
_LEAD = re.compile(
    r"(?i)(?:"
    r"site plan|special permit|mixed[- ]use|commercial|industrial|institutional|"
    r"new construction|new building|exterior renovation|fa(?:c|ç)ade|curtain wall|"
    r"warehouse|hotel|office building|laboratory|school building"
    r")"
)
_SKIP_LINE = re.compile(
    r"(?i)approval of minutes|roll call|adjourn|pledge of allegiance|executive session"
)


class ParseError(Exception):
    pass


def agenda_links(html: str, page_url: str) -> list[dict]:
    """Planning-board agenda files linked from the public HTML."""
    found = []
    for match in re.finditer(
        r'(?is)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        html or "",
    ):
        href = unescape(match.group(1))
        label = _plain(match.group(2))
        blob = f"{label} {href}"
        if "ViewFile" not in href and not href.lower().endswith(".pdf"):
            continue
        if not _BOARD.search(blob) and "planning" not in href.casefold():
            # The link text is often just "Agenda". Look at the nearby board
            # name by keeping links whose URL or text mentions planning.
            continue
        if not _BOARD.search(blob) and "agenda" not in label.casefold() and "planning" not in blob.casefold():
            continue
        found.append(
            {
                "url": urljoin(page_url, href),
                "label": label or "Agenda",
                "posted": (parse_when(label) or "")[:10] or None,
            }
        )
    return found


def lead_lines(text: str) -> list[str]:
    lines = []
    seen = set()
    for raw in (text or "").splitlines():
        line = re.sub(r"\s+", " ", raw).strip(" -\t")
        if len(line) < 12 or len(line) > 280:
            continue
        if _SKIP_LINE.search(line) or not _LEAD.search(line):
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return lines[:12]


def _plain(value: str) -> str:
    text = unescape(re.sub(r"(?is)<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", text).strip()
