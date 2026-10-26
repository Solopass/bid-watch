"""Flag town bids that are also on the public COMMBUYS open-bid list.

COMMBUYS (a Periscope / Jaggaer site) renders a public advanced search at
``advancedSearchBid.xhtml?openBids=true`` and that page offers a CSV export
of the open solicitations. No login and no captcha. The export is one GET
plus one POST. If either step fails, callers leave ``on_commbuys`` unknown.
"""

from __future__ import annotations

import csv
import io
import re
from urllib.parse import quote

from scraper.fetch import FetchError

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


def assign_flags(bids: list[dict], rows: list[dict] | None) -> None:
    """Set on_commbuys and commbuys_url on each bid in place.

    ``rows is None`` means the check did not run; existing yes/no values
    stay, and bids with no prior value become unknown.
    """
    if rows is None:
        for bid in bids:
            bid["on_commbuys"] = bid.get("on_commbuys") or "unknown"
            bid["commbuys_url"] = bid.get("commbuys_url")
        return

    candidates: list[tuple[float, int, int]] = []
    for index, bid in enumerate(bids):
        town_rows = [
            (row_index, row)
            for row_index, row in enumerate(rows)
            if _municipality(row.get("Organization Name") or "", bid.get("town") or "")
        ]
        for row_index, row in town_rows:
            score = _score(bid, row)
            if score > 0:
                candidates.append((score, index, row_index))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    used_bids: set[int] = set()
    used_rows: set[int] = set()
    chosen: dict[int, dict] = {}
    for _score_value, index, row_index in candidates:
        if index in used_bids or row_index in used_rows:
            continue
        used_bids.add(index)
        used_rows.add(row_index)
        chosen[index] = rows[row_index]

    for index, bid in enumerate(bids):
        row = chosen.get(index)
        if row:
            doc = (row.get("Bid Solicitation #") or "").strip()
            bid["on_commbuys"] = "yes"
            bid["commbuys_url"] = (
                "https://www.commbuys.com/bso/external/bidDetail.sda"
                f"?docId={quote(doc)}&external=true"
                if doc
                else None
            )
        else:
            bid["on_commbuys"] = "no"
            bid["commbuys_url"] = None


def _municipality(org: str, town: str) -> bool:
    cleaned = re.sub(r"\s+", " ", org).strip().casefold()
    name = town.strip().casefold()
    if not name:
        return False
    return cleaned in {f"town of {name}", f"city of {name}", name}


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
