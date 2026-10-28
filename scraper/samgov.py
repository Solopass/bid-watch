"""SAM.gov public Opportunities API.

One or two calls per run. The key is SAM_GOV_API_KEY. When it is missing
the feed is skipped and no request is made. Award notices are dropped.
NAICS codes starting with 236, 237, or 238 are kept. Place of performance
must be Massachusetts, or an NH/RI border town already in the town list.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from urllib.parse import urlencode

from scraper.dates import parse_when

SEARCH_URL = "https://api.sam.gov/opportunities/v2/search"
_KEEP_TYPES = ("solicitation", "combined synopsis", "pre-solicitation", "presolicitation", "sources sought")
_NAICS_PREFIXES = ("236", "237", "238")
_LIVE_STATES = ("MA", "NH")


class SamError(Exception):
    pass


def api_key_from_env() -> str:
    return (os.environ.get("SAM_GOV_API_KEY") or "").strip()


def search_opportunities(fetcher, now: datetime, api_key: str | None = None) -> tuple[list[dict] | None, str, str]:
    """Return (records or None when skipped, status, message)."""
    key = api_key if api_key is not None else api_key_from_env()
    if not key:
        return None, "skipped", (
            "Skipped: SAM_GOV_API_KEY is not set. "
            "Add it as a repository secret to read federal construction opportunities. "
            "No request was made."
        )
    posted_to = now.date()
    posted_from = posted_to - timedelta(days=364)
    records: list[dict] = []
    notes = []
    for state in _LIVE_STATES:
        query = urlencode(
            {
                "api_key": key,
                "postedFrom": posted_from.strftime("%m/%d/%Y"),
                "postedTo": posted_to.strftime("%m/%d/%Y"),
                "ptype": "o",
                "state": state,
                "limit": "1000" if state == "MA" else "200",
                "offset": "0",
            }
        )
        url = f"{SEARCH_URL}?{query}"
        try:
            body = fetcher.get(url)
        except Exception as exc:
            raise SamError(str(exc)) from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise SamError("SAM.gov did not return JSON") from exc
        if isinstance(payload, dict) and payload.get("errorMessage"):
            raise SamError(str(payload.get("errorMessage")))
        batch = payload.get("opportunitiesData") if isinstance(payload, dict) else None
        if batch is None:
            raise SamError("SAM.gov response had no opportunitiesData")
        records.extend(batch)
        total = payload.get("totalRecords")
        if isinstance(total, int) and total > len(batch):
            notes.append(f"{state} list truncated at {len(batch)} of {total}")
    message = f"{len(records)} raw notices from {', '.join(_LIVE_STATES)}"
    if notes:
        message = f"{message}; " + "; ".join(notes)
    message += ". RI was not requested so this run stays at two API calls."
    return records, "ok", message


def filter_opportunities(records: list[dict], town_regions: dict[str, str]) -> list[dict]:
    border = {
        town.casefold()
        for town, region in town_regions.items()
        if region in ("NH border", "RI border")
    }
    items = []
    for record in records:
        if not _construction(record):
            continue
        if not _active_solicitation(record):
            continue
        state, city = _place(record)
        if state == "MA":
            pass
        elif state in ("NH", "RI") and _border_city(city, border):
            pass
        else:
            continue
        title = (record.get("title") or "").strip() or "Federal construction opportunity"
        notice = (record.get("noticeId") or "").strip()
        link = (record.get("uiLink") or "").strip()
        if not link and notice:
            link = f"https://sam.gov/opp/{notice}/view"
        naics = str(record.get("naicsCode") or "").strip()
        org = (record.get("department") or record.get("organizationName") or "").strip()
        description = record.get("description") or ""
        if isinstance(description, str) and description.startswith("http"):
            description = ""
        items.append(
            {
                "title": title,
                "url": link or SEARCH_URL,
                "bid_number": (record.get("solicitationNumber") or "").strip() or None,
                "due": parse_when(record.get("responseDeadLine")),
                "posted_date": (parse_when(record.get("postedDate")) or "")[:10] or None,
                "category": " — ".join(part for part in (naics, org, city) if part) or None,
                "scope": description if isinstance(description, str) else "",
                "town": _town_name(city, state, town_regions),
                "state_code": state,
                "kind": "open",
                "sector": "federal",
                "source": "sam_gov",
            }
        )
    return items


def _construction(record: dict) -> bool:
    naics = str(record.get("naicsCode") or record.get("ncode") or "").strip()
    return naics.startswith(_NAICS_PREFIXES)


def _active_solicitation(record: dict) -> bool:
    active = str(record.get("active") or "").strip().casefold()
    if active and active not in ("yes", "true", "active"):
        return False
    kind = f"{record.get('type') or ''} {record.get('baseType') or ''}".casefold()
    if "award" in kind:
        return False
    if kind.strip() and not any(token in kind for token in _KEEP_TYPES):
        return False
    return True


def _place(record: dict) -> tuple[str, str]:
    place = record.get("placeOfPerformance") or {}
    if not isinstance(place, dict):
        return "", ""
    state = place.get("state") or {}
    if isinstance(state, dict):
        code = str(state.get("code") or state.get("name") or "")
    else:
        code = str(state or "")
    city = place.get("city") or {}
    if isinstance(city, dict):
        name = str(city.get("name") or "")
    else:
        name = str(city or "")
    return code.strip().upper(), name.strip()


def _border_city(city: str, border: set[str]) -> bool:
    cleaned = city.casefold().replace(".", "")
    if cleaned in border:
        return True
    for town in border:
        short = town.replace(" nh", "").replace(" ri", "").strip()
        if cleaned == short or cleaned.startswith(short + " "):
            return True
    return False


def _town_name(city: str, state: str, town_regions: dict[str, str]) -> str:
    if not city:
        return "Federal"
    for town in town_regions:
        if town.casefold() == city.casefold():
            return town
        if state in ("NH", "RI") and town.casefold().startswith(city.casefold() + " "):
            return town
    return city.title()
