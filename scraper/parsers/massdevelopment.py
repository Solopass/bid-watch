"""MassDevelopment RFPs and RFQs.

Each posting is an h5 heading followed by a PDF button. The heading text
and the PDF are not nested, so this pairs each heading with the next PDF
link. Holliston's CivicPlus document page uses a different layout.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

from scraper.dates import parse_when

_SECTION = re.compile(
    r"<h5[^>]*>([\s\S]*?)</h5>([\s\S]*?)(?=<h5\b|</main>|$)",
    re.IGNORECASE,
)
_PDF = re.compile(r'href="([^"]+\.pdf[^"]*)"', re.IGNORECASE)
_PROJECT = re.compile(r"(?i)project no\.?\s*([A-Z0-9][A-Z0-9-]*)")
_TAG = re.compile(r"<[^>]+>")


class ParseError(Exception):
    pass


def parse_massdevelopment(html: str, page_url: str) -> list[dict]:
    if "<h5" not in html.lower():
        raise ParseError("MassDevelopment RFP page had no posting headings")
    items = []
    for heading, rest in _SECTION.findall(html):
        title = _text(heading)
        if not title:
            continue
        pdf = _PDF.search(rest)
        if not pdf:
            continue
        blob = f"{title} {_text(rest)}"
        project = _PROJECT.search(blob)
        items.append(
            {
                "town": "MassDevelopment",
                "region": "Statewide agency",
                "source": "massdevelopment",
                "kind": "open",
                "title": title,
                "bid_number": project.group(1) if project else None,
                "category": "MassDevelopment",
                "posted_date": None,
                "due": parse_when(blob),
                "url": urljoin(page_url, unescape(pdf.group(1))),
                "source_url": page_url,
            }
        )
    return items


def _text(value: str) -> str:
    cleaned = _TAG.sub(" ", value or "")
    return re.sub(r"\s+", " ", unescape(cleaned).replace("\xa0", " ")).strip()
