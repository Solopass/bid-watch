"""Public bid lists and Boston development projects for the large cities.

Cambridge's current-bid page and its OpenGov portal answer with a human
check and are not parsed here. Boston's supplier portal is a login and is
not requested.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

from scraper.dates import parse_when

_RESIDENTIAL = re.compile(r"(?i)residential")
_MIXED = re.compile(r"(?i)mixed[ -]?use|commercial|institutional|industrial|office|hotel|lab|retail|civic")
_KEEP_STATUS = re.compile(
    r"(?i)under review|approved|letter of intent|board approved|in review"
)
_DROP_STATUS = re.compile(r"(?i)withdrawn|complete|closed|on hold")


class ParseError(Exception):
    pass


def parse_boston_bids(html: str, page_url: str) -> list[dict]:
    if "views-row" not in html and "bid-listings" not in html.casefold():
        raise ParseError("Boston bid listings page had no bid rows")
    items = []
    for chunk in re.split(r'(?i)<div class="views-row">', html)[1:]:
        title_match = re.search(
            r'(?is)<div class="n-li-t">\s*<a href="([^"]+)"[^>]*>(.*?)</a>',
            chunk,
        )
        if not title_match:
            continue
        title = _plain(title_match.group(2))
        if not title:
            continue
        number = ""
        number_match = re.search(
            r'(?is)field-event-project-number.*?>([A-Z0-9][A-Z0-9-]{2,})<',
            chunk,
        )
        if number_match:
            number = _plain(number_match.group(1))
        due = _labeled_value(chunk, "Due")
        posted = _labeled_value(chunk, "Posted")
        items.append(
            {
                "title": title,
                "url": urljoin(page_url, title_match.group(1)),
                "bid_number": number or None,
                "due": parse_when(due),
                "posted_date": (parse_when(posted) or "")[:10] or None,
                "category": None,
            }
        )
    return items


def parse_worcester_bids(html: str, page_url: str) -> list[dict]:
    if "view-bids" not in html and "views-field-title" not in html:
        raise ParseError("Worcester open-bids page had no bid table")
    return _drupal_rows(html, page_url, due_class="field-bid-posting-close-date")


def parse_somerville_bids(html: str, page_url: str) -> list[dict]:
    if "view-bid-posting" not in html and "open-bids" not in html.casefold():
        raise ParseError("Somerville procurement page had no open-bid table")
    return _drupal_rows(html, page_url, due_class="field-start-date-new")


def parse_quincy_bids(html: str, page_url: str) -> list[dict]:
    if "rpfbids" not in html and "bid_detail" not in html:
        raise ParseError("Quincy current-bids page had no bid table")
    items = []
    parts = re.split(r'(?i)<a href="(bid_detail_T[^"]+)">', html)
    for index in range(1, len(parts), 2):
        href = parts[index]
        rest = parts[index + 1] if index + 1 < len(parts) else ""
        title = _plain(rest.split("</a>", 1)[0])
        if not title:
            continue
        dates = re.findall(
            r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}(?:\s+\d{1,2}:\d{2}\s*[AP]M)?",
            rest[:2000],
            re.I,
        )
        due = parse_when(dates[-1]) if dates else None
        items.append(
            {
                "title": title,
                "url": urljoin(page_url, href),
                "bid_number": None,
                "due": due,
                "category": None,
            }
        )
    return items


def parse_bha_bids(html: str, page_url: str) -> list[dict]:
    if "BidHeading" not in html and "Current Bid" not in html:
        raise ParseError("Boston Housing bid page had no headings")
    items = []
    for match in re.finditer(
        r'(?is)<a class="BidHeading" href="([^"]+)"[^>]*>\s*<h2>(.*?)</h2>\s*</a>\s*<p>(.*?)</p>',
        html,
    ):
        title = _plain(match.group(2))
        blurb = _plain(match.group(3))
        number = ""
        numbered = re.search(r"(?i)\b(?:IFB|RFP|AE RFP)\s*#?\s*([A-Z0-9][A-Z0-9-]{2,})", title)
        if numbered:
            number = numbered.group(1)
        due = parse_when(blurb) if re.search(r"(?i)\b(?:due|opening)\b", blurb) else None
        items.append(
            {
                "title": title,
                "url": urljoin(page_url, match.group(1)),
                "bid_number": number or None,
                "due": due,
                "category": blurb[:240] or None,
            }
        )
    return items


def parse_boston_projects(html: str, page_url: str) -> list[dict]:
    """Development projects under review or approved. Residential-only rows are left out."""
    if "devprojectTable" not in html:
        raise ParseError("Boston planning page had no project tables")
    items = []
    for table in re.findall(r"(?is)<table class='devprojectTable.*?>(.*?)</table>", html):
        link = re.search(r"(?is)<caption>\s*<a href='([^']+)'>(.*?)</a>", table)
        if not link:
            continue
        name = _plain(link.group(2))
        heads = [_plain(h) for h in re.findall(r"(?is)<h2>(.*?)</h2>", table)]
        status = re.sub(r"(?i)\s*project status\s*$", "", heads[0] if heads else "").strip()
        use = re.sub(r"(?i)\s*project type\s*$", "", heads[1] if len(heads) > 1 else "").strip()
        filed = re.sub(r"(?i)\s*latest filed date\s*$", "", heads[2] if len(heads) > 2 else "").strip()
        if not _keep_project(status, use):
            continue
        items.append(
            {
                "title": name,
                "url": urljoin(page_url, link.group(1)),
                "category": " — ".join(part for part in (status, use) if part) or None,
                "posted_date": (parse_when(filed) or "")[:10] or None,
                "kind": "lead",
                "sector": "private",
                "source": "boston_planning",
                "town": "Boston",
                "region": "Cities",
                "state": "Massachusetts",
            }
        )
    return items


def _keep_project(status: str, use: str) -> bool:
    if not status or _DROP_STATUS.search(status):
        return False
    if not _KEEP_STATUS.search(status):
        return False
    if use and _RESIDENTIAL.search(use) and not _MIXED.search(use):
        return False
    if use and not _MIXED.search(use) and use.strip():
        # A blank use can still be a commercial filing. A named non-commercial
        # type such as "Open Space" is left out.
        if not _RESIDENTIAL.search(use):
            return False
    return True


def _drupal_rows(html: str, page_url: str, due_class: str) -> list[dict]:
    items = []
    for row in re.findall(r"(?is)<tr>(.*?)</tr>", html):
        if "views-field-title" not in row or "<th" in row.casefold():
            continue
        title_match = re.search(
            r'(?is)views-field-title.*?href="([^"]+)"[^>]*>(.*?)</a>',
            row,
        )
        if not title_match:
            title_match = re.search(r"(?is)views-field-title[^>]*>(.*?)</td>", row)
            href = page_url
            title = _plain(title_match.group(1)) if title_match else ""
        else:
            href = title_match.group(1)
            title = _plain(title_match.group(2))
        if not title:
            continue
        number_match = re.search(
            r"(?is)views-field-field-bid(?:-posting-bid)?-number[^>]*>(.*?)</td>",
            row,
        )
        number = _plain(number_match.group(1)) if number_match else ""
        if number.lower().startswith("bid number"):
            number = ""
        due_match = re.search(rf"(?is){re.escape(due_class)}.*?>(.*?)</td>", row)
        due_text = _plain(due_match.group(1)) if due_match else ""
        items.append(
            {
                "title": title,
                "url": urljoin(page_url, href),
                "bid_number": number or None,
                "due": parse_when(due_text),
                "category": None,
            }
        )
    return items


def _labeled_value(html: str, label: str) -> str:
    match = re.search(
        rf"(?is)<span class=\"dl-t\">\s*{label}:\s*</span>\s*<span class=\"dl-d\">(.*?)</span>",
        html,
    )
    return _plain(match.group(1)) if match else ""


def _plain(value: str) -> str:
    text = unescape(re.sub(r"(?is)<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", text).strip()
