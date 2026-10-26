"""MSBA estimated bid dates.

The capital-planning page links a monthly PDF. Rows are upcoming school
building projects (a pipeline), not open bids. Only a district whose name
is exactly one of the covered towns is kept. "North Andover" is not Dover
and not Andover.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from scraper.pdftext import pdf_to_text

_PDF_LINK = re.compile(r'href="([^"]*Estimated_Bid_Dates_[^"]+\.pdf)"', re.IGNORECASE)
_ROW = re.compile(
    r"^(?P<district>.+?)(?P<school>MHS|ES|MS|HS)"
    r"(?P<enroll>[\d,]+)"
    r"(?P<ptype>Add/Reno|New)"
    r"\$\s*(?P<budget>[\d,.]+)"
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?P<start>\d{1,2}/\d{1,2}/\d{2})"
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?P<finish>\d{1,2}/\d{1,2}/\d{2})"
    r"\$\s*(?P<construction>[\d,.]+)"
    r"(?P<notes>.*?)$"
)
_SCHOOL = {
    "ES": "elementary school",
    "MS": "middle school",
    "HS": "high school",
    "MHS": "middle/high school",
}


class ParseError(Exception):
    pass


def latest_bid_schedule_url(html: str, page_url: str) -> str | None:
    from urllib.parse import urljoin

    links = [urljoin(page_url, href) for href in _PDF_LINK.findall(html)]
    if not links:
        return None
    return links[-1]


def parse_bid_schedule(
    pdf_bytes: bytes,
    pdf_url: str,
    page_url: str,
    town_regions: dict[str, str],
) -> list[dict]:
    if not pdf_bytes.startswith(b"%PDF"):
        raise ParseError("MSBA estimated bid schedule was not a PDF")
    text = pdf_to_text(pdf_bytes)
    if "Estimated" not in text and "Project Budget" not in text and "ES" not in text:
        raise ParseError("MSBA estimated bid schedule had no readable project rows")
    return parse_bid_schedule_text(text, pdf_url, page_url, town_regions)


def parse_bid_schedule_text(
    text: str,
    pdf_url: str,
    page_url: str,
    town_regions: dict[str, str],
) -> list[dict]:
    regions = {name.casefold(): (name, region) for name, region in town_regions.items()}
    items = []
    seen = set()
    for raw_line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        match = _ROW.match(line)
        if not match:
            continue
        district = match.group("district").strip()
        place = regions.get(district.casefold())
        if not place:
            continue
        town, region = place
        school = match.group("school")
        project_type = "addition/renovation" if match.group("ptype") == "Add/Reno" else "new"
        start = _short_date(match.group("start"))
        notes = _notes(match.group("notes"))
        key = (town.casefold(), school, match.group("enroll"), match.group("ptype"))
        if key in seen:
            continue
        seen.add(key)
        slug = quote(f"{town}-{school}-{match.group('enroll')}-{match.group('ptype')}", safe="")
        label = _SCHOOL.get(school, school)
        items.append(
            {
                "town": town,
                "region": region,
                "source": "msba",
                "kind": "upcoming",
                "title": f"{town} {label} — {project_type} (MSBA estimated bid)",
                "bid_number": f"MSBA {town} {school} {match.group('enroll')} {match.group('ptype')}",
                "category": notes or "MSBA capital pipeline",
                "posted_date": None,
                "due": start,
                "url": f"{pdf_url}#{slug}",
                "source_url": page_url,
            }
        )
    return items


def _short_date(value: str) -> str | None:
    month, day, year = value.split("/")
    full_year = 2000 + int(year)
    if not (1 <= int(month) <= 12 and 1 <= int(day) <= 31 and 2020 <= full_year <= 2100):
        return None
    return f"{full_year:04d}-{int(month):02d}-{int(day):02d}"


def _notes(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip(" .")
    text = text.replace("targeted:", "targeted")
    if text.lower().startswith("gmp"):
        return "GMP " + text[3:].strip()
    if text.lower().startswith("ntp"):
        return "NTP " + text[3:].strip()
    return text
