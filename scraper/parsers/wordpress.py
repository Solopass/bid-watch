"""Sudbury's WordPress bid table.

The list has a title, publish date, department, and category, and no due
date. Each post is opened and the due date is read from the body. Question
deadlines also use the word "until", so the date tied to the public opening
wins over an earlier question deadline.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

from scraper.dates import as_date, parse_when

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
_ANCHOR = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_UNTIL = re.compile(
    r"(?i)until\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})\s+at\s+"
    r"(\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?)"
)
_OPENING = re.compile(
    r"(?i)bid\s+opening:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})"
    r"(?:\s+at\s+(\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?))?"
)
_CONTRACT = re.compile(
    r"(?i)\b(?:contract|project|bid)\s*(?:no\.?|number|#)\s*[:.]?\s*"
    r"([A-Z0-9][A-Z0-9-]{2,})"
)
_CONTRACT_CODE = re.compile(r"\b(20\d{2}-[A-Z]{1,8}-\d+)\b")


class ParseError(Exception):
    pass


def parse_wordpress_list(html: str, page_url: str) -> list[dict]:
    if "<table" not in html.lower():
        raise ParseError("WordPress bid table was not on the page")
    items = []
    for row in _ROW.findall(html):
        cells = _CELL.findall(row)
        if len(cells) < 2:
            continue
        link = _ANCHOR.search(cells[0])
        if not link:
            continue
        title = _text(link.group(2))
        if not title or title.lower() == "title":
            continue
        posted = as_date(_text(cells[1])) if len(cells) > 1 else None
        department = _text(cells[2]) if len(cells) > 2 else None
        category = _text(cells[3]) if len(cells) > 3 else None
        items.append(
            {
                "title": title,
                "url": urljoin(page_url, unescape(link.group(1))),
                "bid_number": None,
                "posted_date": posted,
                "due": None,
                "category": department or category,
            }
        )
    return items


def enrich_wordpress_post(html: str) -> dict:
    visible = _text(re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html))
    return {
        "due": _due_from_text(visible),
        "bid_number": _contract_number(visible),
    }


def _due_from_text(text: str) -> str | None:
    opening = _OPENING.search(text)
    if opening:
        spoken = opening.group(1)
        if opening.group(2):
            spoken = f"{spoken} at {opening.group(2)}"
        parsed = parse_when(spoken)
        if parsed:
            return parsed
    best = None
    best_score = -999
    for match in _UNTIL.finditer(text):
        spoken = f"{match.group(1)} at {match.group(2)}"
        parsed = parse_when(spoken)
        if not parsed:
            continue
        before = text[max(0, match.start() - 80): match.start()].lower()
        after = text[match.end(): match.end() + 90].lower()
        score = 0
        if "question" in before:
            score -= 5
        if "sealed" in before or "bids will be received" in before:
            score += 3
        if "publicly opened" in after or "bid opening" in after:
            score += 5
        # A later deadline outranks an earlier one when the context is tied.
        score += 0.01 if parsed > (best or "") else 0
        if score > best_score:
            best = parsed
            best_score = score
    return best


def _contract_number(text: str) -> str | None:
    match = _CONTRACT.search(text)
    if match:
        return match.group(1).strip(" .")
    match = _CONTRACT_CODE.search(text)
    if match:
        return match.group(1)
    return None


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG.sub(" ", value))).strip()
