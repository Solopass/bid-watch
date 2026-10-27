"""Public bid lists for MassDOT, DCR, and MAPC.

MassDOT rows are kept when the location names a covered town, Worcester,
or Northborough. District-wide maintenance with no town is left out.
DCR has no town column, so the current construction table is kept whole
and the awards table is ignored. MAPC notices are kept when they name a
covered town, MetroWest, Worcester, or Northborough.
"""

from __future__ import annotations

import re
from datetime import date
from html import unescape
from urllib.parse import urljoin

from scraper.dates import parse_when

_STREET = (
    r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|"
    r"place|pl|way|circle|cir|court|ct)"
)
_NOTICE = re.compile(
    r"(?i)legal notice|invites bids|invitation for bids|request for (?:proposals|quotes|bids)"
)


class ParseError(Exception):
    pass


def parse_massdot(html: str, page_url: str, town_regions: dict[str, str]) -> list[dict]:
    if "project number" not in html.casefold():
        raise ParseError("MassDOT schedule had no project tables")
    due = None
    items: list[dict] = []
    for match in re.finditer(
        r"(?is)Bid Opening:\s*([^<]+)|(<table\b[\s\S]*?</table>)",
        html,
    ):
        if match.group(1):
            due = parse_when(match.group(1))
            continue
        table = match.group(2) or ""
        if "project number" not in table.casefold():
            continue
        location = _labeled(table, "Location")
        description = _labeled(table, "Description")
        number = _labeled(table, "Project Number")
        if not location or not mentions_covered(
            location, town_regions, extras=("worcester", "northborough")
        ):
            continue
        title = f"{_title_location(location)} — {description}".strip(" —")
        anchor = re.sub(r"[^A-Za-z0-9]+", "", number) or str(len(items) + 1)
        items.append(
            _agency_item(
                town="MassDOT",
                region="Statewide agency",
                source="massdot",
                title=title,
                bid_number=number or None,
                category=location,
                due=due,
                url=f"{page_url}#{anchor}",
                source_url=page_url,
            )
        )
    return items


def parse_dcr(html: str, page_url: str) -> list[dict]:
    lower = html.casefold()
    start = lower.find("bid responses sought")
    end = lower.find("recent construction contract awards")
    if start < 0 or end < start:
        raise ParseError("DCR page had no current construction bid table")
    items: list[dict] = []
    for row in re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>", html[start:end]):
        cells = [
            _plain(cell)
            for cell in re.findall(r"(?is)<t[dh]\b[^>]*>(.*?)</t[dh]>", row)
        ]
        cells = [cell for cell in cells if cell]
        if len(cells) < 3 or cells[0].casefold().startswith("contract"):
            continue
        number, name, when = cells[0], cells[1], cells[2]
        anchor = re.sub(r"[^A-Za-z0-9]+", "", number) or str(len(items) + 1)
        items.append(
            _agency_item(
                town="DCR",
                region="Statewide agency",
                source="dcr",
                title=name,
                bid_number=number,
                category="Construction",
                due=parse_when(when),
                url=f"{page_url}#{anchor}",
                source_url=page_url,
            )
        )
    return items


def parse_mapc(html: str, page_url: str, town_regions: dict[str, str]) -> list[dict]:
    modules = re.findall(
        r'(?is)<div\b[^>]*fl-rich-text[^>]*>(.*?)</div>',
        html,
    )
    if not modules and "legal notice" not in html.casefold():
        raise ParseError("MAPC legal-notices page had no notice text")
    items: list[dict] = []
    seen: set[str] = set()
    for module in modules:
        strong = re.search(r"(?is)<strong\b[^>]*>(.*?)</strong>", module)
        title = _plain(strong.group(1)) if strong else ""
        body = _plain(module)
        if not title:
            title = body.split("\n", 1)[0].strip()
        blob = f"{title}\n{body}"
        if not _NOTICE.search(blob):
            continue
        where = mentions_covered(
            blob,
            town_regions,
            extras=("metrowest", "worcester", "northborough"),
        )
        if not where:
            continue
        if re.search(r"(?i)metrowest", blob):
            region = "MetroWest core"
        elif where == "worcester":
            region = "Outer ring"
        elif where in town_regions:
            region = town_regions[where]
        else:
            region = "Outer ring"
        due = _notice_due(body)
        if due is None:
            # Contract end dates are not a due date. A file stamped last year,
            # with no due date, is an archive still sitting on the page.
            file_years = [int(year) for year in re.findall(r"/(20\d{2})/", module)]
            file_years += [int(year) for year in re.findall(r"\d{1,2}\.\d{1,2}\.(20\d{2})", module)]
            if file_years and max(file_years) < date.today().year:
                continue
        slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")[:80]
        if slug in seen:
            slug = f"{slug}-{len(seen) + 1}"
        seen.add(slug)
        number = _mapc_number(title)
        items.append(
            _agency_item(
                town="MAPC",
                region=region,
                source="mapc",
                title=title,
                bid_number=number,
                category={
                    "metrowest": "MetroWest",
                    "worcester": "Worcester",
                    "northborough": "Northborough",
                }.get(where, where),
                due=due,
                url=urljoin(page_url, f"#{slug}"),
                source_url=page_url,
            )
        )
    return items


def mapc_in_range(text: str, town_regions: dict[str, str]) -> bool:
    """MAPC buys cover many regions. Keep MetroWest, Worcester, and Northborough."""
    if mentions_covered(
        text, town_regions, extras=("metrowest", "worcester", "northborough")
    ):
        return True
    return bool(re.search(r"(?i)(?<![a-z0-9])(?:mw\s*\d{4}|crhsac\s*\d+)", text or ""))


# Towns named in a title that are outside the ring. Used when a list has no
# location column but the title itself names a place.
_OUTSIDE_PLACES = (
    "boston",
    "cambridge",
    "chelsea",
    "somerville",
    "quincy",
    "brockton",
    "springfield",
    "lowell",
    "lawrence",
    "lynn",
    "newton",
    "plymouth",
    "pittsfield",
    "fall river",
    "new bedford",
)


def names_outside_place(text: str, town_regions: dict[str, str]) -> bool:
    """True when the text names an outside city and no covered town."""
    if mentions_covered(
        text, town_regions, extras=("metrowest", "worcester", "northborough")
    ):
        return False
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (text or "").casefold())
    cleaned = re.sub(r"\s+", " ", cleaned)
    for place in _OUTSIDE_PLACES:
        if re.search(rf"(?<![a-z0-9]){re.escape(place)}(?![a-z0-9])", cleaned):
            return True
    return False


def mentions_covered(
    text: str,
    town_regions: dict[str, str],
    extras: tuple[str, ...] = (),
) -> str | None:
    """First covered town in text, ignoring 'Franklin Street' style names."""
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (text or "").casefold())
    cleaned = re.sub(r"\s+", " ", cleaned)
    # Drop "Franklin Street" so a street name is not a town.
    cleaned = re.sub(rf"\b[a-z]+\s+{_STREET}\b", " ", cleaned)
    for town in sorted(town_regions, key=len, reverse=True):
        if town.endswith(" NH") or town.endswith(" RI"):
            continue
        name = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", town.casefold())).strip()
        if name and re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", cleaned):
            return town
    for extra in extras:
        token = extra.casefold()
        if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", cleaned):
            return extra
    return None


def _notice_due(text: str) -> str | None:
    rescheduled = list(
        re.finditer(r"(?i)reschedul\w*\s+to\s+([^.]{0,80})", text)
    )
    if rescheduled:
        found = parse_when(rescheduled[-1].group(1))
        if found:
            return found
    for match in re.finditer(
        r"(?i)(?:due|accepted until|opened on|will be opened)\b[^.]{0,90}",
        text,
    ):
        found = parse_when(match.group(0))
        if found:
            return found
    return None


def _mapc_number(title: str) -> str | None:
    match = re.search(
        r"(?i)\b((?:MW|SS|GBPC|FCAM|OFF)\s*\d{2,4}(?:\s+[A-Z]{1,4}\d+)?|CRHSAC\s*\d+)\b",
        title,
    )
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(1)).upper()


def _labeled(html: str, label: str) -> str:
    match = re.search(
        rf"(?is)<strong>\s*{re.escape(label)}\s*:\s*</strong>\s*(?:<i>)?\s*([^<]+)",
        html,
    )
    if not match:
        return ""
    return _plain(match.group(1))


def _title_location(location: str) -> str:
    parts = re.split(r"(\s+-\s+)", location.strip())
    return "".join(part.title() if i % 2 == 0 else part for i, part in enumerate(parts))


def _plain(fragment: str) -> str:
    text = unescape(re.sub(r"(?is)<[^>]+>", " ", fragment or ""))
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip(" -")


def _agency_item(
    *,
    town: str,
    region: str,
    source: str,
    title: str,
    bid_number: str | None,
    category: str | None,
    due: str | None,
    url: str,
    source_url: str,
) -> dict:
    return {
        "town": town,
        "region": region,
        "sector": "agency",
        "state": "Massachusetts",
        "source": source,
        "kind": "open",
        "title": title,
        "bid_number": bid_number,
        "category": category,
        "due": due,
        "url": url,
        "source_url": source_url,
    }
