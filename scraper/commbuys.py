"""Read the public COMMBUYS open-bid list and keep the covered towns.

COMMBUYS (a Periscope / Jaggaer site) renders a public advanced search at
``advancedSearchBid.xhtml?openBids=true`` and that page offers a CSV export
of the open solicitations. No login and no captcha. The export is one GET
plus one POST. The CSV has no location column; organization name is the
filter, and a location column is honored if one appears later. Statewide
rows are not kept. A row that matches a town-page bid sets ``on_commbuys``
on that bid instead of becoming a second record.
"""

from __future__ import annotations

import csv
import io
import re
from urllib.parse import quote

from scraper.dates import parse_when
from scraper.fetch import FetchError
from scraper.places import Place, match_row, town_regions_from_config

SEARCH_URL = (
    "https://www.commbuys.com/bso/view/search/external/"
    "advancedSearchBid.xhtml?openBids=true"
)
_CSV_BUTTON = re.compile(
    r"Export to CSV File[\s\S]{0,400}?\{'([^']+)':'([^']+)'\}",
    re.IGNORECASE,
)
_VIEWSTATE = re.compile(r'name="javax\.faces\.ViewState"[^>]*value="([^"]*)"')
_CSRF = re.compile(r'name="_csrf"[^>]*value="([^"]*)"')

_STOP = {
    "the", "and", "for", "of", "a", "an", "to", "in", "on", "at", "with",
    "or", "by", "be", "is", "from", "town", "city", "ma", "massachusetts",
    "ifb", "rfp", "rfq", "bid", "bids", "invitation", "project", "services",
    "service", "sow", "phase",
}


class CommBuysError(FetchError):
    pass


def fetch_open_solicitations(fetcher) -> list[dict]:
    html = fetcher.get(SEARCH_URL)
    button = _CSV_BUTTON.search(html)
    view = _VIEWSTATE.search(html)
    csrf = _CSRF.search(html)
    if not (button and view and csrf):
        raise CommBuysError("COMMBUYS public search did not offer a CSV export")
    _status, body, content_type = fetcher.post_form(
        SEARCH_URL,
        {
            "bidSearchResultsForm": "bidSearchResultsForm",
            "_csrf": csrf.group(1),
            "openBids": "true",
            "javax.faces.ViewState": view.group(1),
            button.group(1): button.group(2),
        },
    )
    stripped = body.lstrip("\ufeff")
    if "csv" not in content_type.lower() and not stripped.startswith('"Bid Solicitation'):
        raise CommBuysError("COMMBUYS export was not a CSV of open solicitations")
    rows = list(csv.DictReader(io.StringIO(stripped)))
    if not rows:
        raise CommBuysError("COMMBUYS CSV had no solicitation rows")
    return rows


def assign_flags(
    bids: list[dict],
    rows: list[dict] | None,
    town_regions: dict[str, str] | None = None,
) -> None:
    """Set on_commbuys and commbuys_url on each bid in place.

    ``rows is None`` means the check did not run; existing yes/no values
    stay, and bids with no prior value become unknown.
    """
    if rows is None:
        for bid in bids:
            bid["on_commbuys"] = bid.get("on_commbuys") or "unknown"
            bid["commbuys_url"] = bid.get("commbuys_url")
        return
    regions = town_regions or town_regions_from_config()
    chosen = _choose(bids, rows, regions)
    for index, bid in enumerate(bids):
        row_index = chosen.get(index)
        row = rows[row_index] if row_index is not None else None
        if row:
            bid["on_commbuys"] = "yes"
            bid["commbuys_url"] = _detail_url(row)
        elif (bid.get("source") or "town") == "commbuys" and bid.get("commbuys_url"):
            bid["on_commbuys"] = "yes"
        else:
            bid["on_commbuys"] = "no"
            bid["commbuys_url"] = None


def covered_rows(
    town_bids: list[dict],
    rows: list[dict] | None,
    town_regions: dict[str, str] | None = None,
) -> list[dict]:
    """COMMBUYS rows for covered organizations that are not already town bids.

    Matching uses town-page bids only, including closed ones, so a town
    record is updated in place instead of being copied.
    """
    if not rows:
        return []
    regions = town_regions or town_regions_from_config()
    page_bids = [bid for bid in town_bids if (bid.get("source") or "town") == "town"]
    used = set(_choose(page_bids, rows, regions).values())
    fresh: list[dict] = []
    for index, row in enumerate(rows):
        if index in used:
            continue
        place = match_row(row, regions)
        if place is None:
            continue
        fresh.append(_bid_from_row(row, place))
    return fresh


def _choose(bids: list[dict], rows: list[dict], town_regions: dict[str, str]) -> dict[int, int]:
    places = [match_row(row, town_regions) for row in rows]
    candidates: list[tuple[float, int, int]] = []
    for index, bid in enumerate(bids):
        for row_index, row in enumerate(rows):
            place = places[row_index]
            if place is None or place.town.casefold() != (bid.get("town") or "").casefold():
                continue
            score = _score(bid, row)
            if score > 0:
                candidates.append((score, index, row_index))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    used_bids: set[int] = set()
    used_rows: set[int] = set()
    chosen: dict[int, int] = {}
    for _score_value, index, row_index in candidates:
        if index in used_bids or row_index in used_rows:
            continue
        used_bids.add(index)
        used_rows.add(row_index)
        chosen[index] = row_index
    return chosen


def _detail_url(row: dict) -> str | None:
    doc = (row.get("Bid Solicitation #") or "").strip()
    if not doc:
        return None
    return (
        "https://www.commbuys.com/bso/external/bidDetail.sda"
        f"?docId={quote(doc)}&external=true"
    )


def _bid_from_row(row: dict, place: Place) -> dict:
    doc = (row.get("Bid Solicitation #") or "").strip()
    description = re.sub(r"\s+", " ", (row.get("Description") or "").replace("\xa0", " ")).strip()
    url = _detail_url(row) or SEARCH_URL
    org = re.sub(r"\s+", " ", (row.get("Organization Name") or "").strip()) or None
    return {
        "town": place.town,
        "region": place.region,
        "sector": place.sector,
        "state": "Massachusetts",
        "source": "commbuys",
        "title": description or f"COMMBUYS {doc}".strip(),
        "bid_number": doc or None,
        "category": org,
        "posted_date": None,
        "due": parse_when(row.get("Bid Opening Date")),
        "url": url,
        "source_url": SEARCH_URL,
        "on_commbuys": "yes",
        "commbuys_url": _detail_url(row),
    }


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [word for word in words if word not in _STOP and len(word) > 1]


def _score(bid: dict, row: dict) -> float:
    description = row.get("Description") or ""
    blob = " ".join(
        [
            description,
            row.get("Alternate Id") or "",
            row.get("Bid Solicitation #") or "",
        ]
    )
    number = (bid.get("bid_number") or "").strip()
    if number and len(number) >= 3:
        pattern = rf"(?<![A-Za-z0-9]){re.escape(number)}(?![A-Za-z0-9])"
        if re.search(pattern, blob, re.IGNORECASE):
            return 100.0
    title_tokens = _tokens(bid.get("title") or "")
    desc_tokens = _tokens(description)
    if len(title_tokens) < 2 or not desc_tokens:
        return 0.0
    overlap = set(title_tokens) & set(desc_tokens)
    if len(overlap) < 2:
        return 0.0
    ratio = len(overlap) / len(set(title_tokens))
    if ratio < 0.5 and len(overlap) < 3:
        return 0.0
    return ratio * 10 + len(overlap)
