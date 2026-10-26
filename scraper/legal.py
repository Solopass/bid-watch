"""Legal notices from the Massachusetts Newspaper Publishers Association.

masspublicnotices.org has a public search. robots.txt allows the search
page. The full notice body sits behind a captcha, so only the listing is
stored: title snippet, paper, publish date, a due date when the snippet
states one, and the detail link. The detail page is not opened.

The search is an ASP.NET form. Selecting a county posts back once, then
each bid-style phrase is submitted for the last 14 days in Middlesex,
Norfolk, and Worcester. Results are filtered to the covered towns.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

from scraper.dates import parse_when
from scraper.fetch import FetchError

SEARCH_URL = "https://www.masspublicnotices.org/Search.aspx"
_PREFIX = "ctl00$ContentPlaceHolder1$as1$"
_NEXT = "ctl00$ContentPlaceHolder1$WSExtendedGridNP1$GridView1$ctl01$btnNext"
_MAX_PAGES = 3
_QUERIES = (
    ("invitation to bid", "EXACT"),
    ("invitation for bid", "EXACT"),
    ("sealed bids", "EXACT"),
    ("request for proposals", "EXACT"),
    ("request for qualifications", "EXACT"),
    ("RFP", "AND"),
    ("RFQ", "AND"),
)
# Middlesex 8, Norfolk 10, Worcester 13. Suffolk is 12; Worcester is last.
_COUNTIES = (8, 10, 13)
_KEEP = re.compile(
    r"invitation to bid|invitation for bids|invitation for bid|sealed bids|"
    r"request for proposals|request for qualifications|\brfp\b|\brfq\b|\bifb\b",
    re.IGNORECASE,
)
_DROP = re.compile(
    r"mortgagee|foreclosure|notice of sale|motor vehicle|estate of|probate",
    re.IGNORECASE,
)
_BLOCK = re.compile(r'<table class="nested">[\s\S]*?</table>', re.IGNORECASE)
_ID = re.compile(r"Details\.aspx\?[^\"']*ID=(\d+)", re.IGNORECASE)
_PAPER = re.compile(r"<strong>([\s\S]*?)</strong>", re.IGNORECASE)
_PAGES = re.compile(r"lblTotalPages\">\s*of\s*(\d+)\s*Pages", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_STOP = {
    "the", "and", "for", "of", "a", "an", "to", "in", "on", "at", "with",
    "or", "by", "be", "is", "from", "town", "city", "ma", "massachusetts",
    "ifb", "rfp", "rfq", "bid", "bids", "invitation", "project", "services",
    "service", "legal", "notice", "sealed", "proposals", "proposal",
    "qualifications", "public", "school", "schools",
}


class LegalNoticeError(FetchError):
    pass


def fetch_notices(fetcher, town_regions: dict[str, str]) -> tuple[list[dict], str]:
    """Return covered-town notices and a short status message."""
    html, page = fetcher.get_page(SEARCH_URL)
    _reject_challenge(html, page)
    fields = _fields(html)
    fields["__EVENTTARGET"] = f"{_PREFIX}lstCounty$8"
    _status, html, _ctype, page = fetcher.post_form_result(page, fields)
    _reject_challenge(html, page)
    if "lstCounty" not in html:
        raise LegalNoticeError("legal-notice county filter did not return the search form")

    found: dict[str, dict] = {}
    truncated = False
    errors: list[str] = []
    for phrase, mode in _QUERIES:
        try:
            fields = _fields(html, phrase=phrase, mode=mode, submit=True)
            _status, html, _ctype, page = fetcher.post_form_result(page, fields)
            _reject_challenge(html, page)
            if "Server Error" in html and "wsResultsGrid" not in html:
                raise LegalNoticeError(f"legal-notice search for {phrase!r} failed")
            pages = _page_count(html)
            for item in parse_notice_page(html, town_regions):
                found[item["url"]] = item
            page_no = 1
            while page_no < pages and page_no < _MAX_PAGES:
                fields = _fields(html, phrase=phrase, mode=mode, submit=False)
                fields[f"{_NEXT}.x"] = "5"
                fields[f"{_NEXT}.y"] = "5"
                _status, html, _ctype, page = fetcher.post_form_result(page, fields)
                _reject_challenge(html, page)
                page_no += 1
                for item in parse_notice_page(html, town_regions):
                    found[item["url"]] = item
            if pages > _MAX_PAGES:
                truncated = True
        except (FetchError, LegalNoticeError) as exc:
            errors.append(f"{phrase}: {exc}")
            print(f"legal-notice query {phrase!r} failed ({exc})", flush=True)
    if not found and errors:
        raise LegalNoticeError("; ".join(errors))
    notices = list(found.values())
    message = f"{len(notices)} covered-town notices in the last 14 days"
    if truncated:
        message += f"; stopped after {_MAX_PAGES} pages on a long query"
    if errors:
        message += f"; {len(errors)} queries failed"
    return notices, message


def parse_notice_page(html: str, town_regions: dict[str, str]) -> list[dict]:
    items = []
    seen = set()
    for block in _BLOCK.findall(html):
        id_match = _ID.search(block)
        if not id_match:
            continue
        notice_id = id_match.group(1)
        if notice_id in seen:
            continue
        seen.add(notice_id)
        paper_match = _PAPER.search(block)
        paper = _text(paper_match.group(1)) if paper_match else None
        body = _body_text(block)
        if not body or not _KEEP.search(body) or _DROP.search(body):
            continue
        town = _primary_town(body, town_regions)
        if not town:
            continue
        published = parse_when(_text(block))
        items.append(
            {
                "town": town,
                "region": town_regions[town],
                "source": "legal_notice",
                "kind": "open",
                "title": body[:240].rstrip(" ."),
                "bid_number": None,
                "category": paper,
                "posted_date": published,
                "due": parse_when(body),
                "url": urljoin(SEARCH_URL, f"Details.aspx?ID={notice_id}"),
                "source_url": SEARCH_URL,
                "paper": paper,
            }
        )
    return items


def apply_notices(bids: list[dict], notices: list[dict]) -> list[dict]:
    """Flag a town or COMMBUYS row when the notice is the same posting.

    Returns notices that did not match, ready to store as their own rows.
    A match sets ``legal_notice_url`` on the existing row and does not
    create a second bid.
    """
    candidates = []
    for notice_index, notice in enumerate(notices):
        for bid_index, bid in enumerate(bids):
            source = bid.get("source") or "town"
            if source not in ("town", "commbuys"):
                continue
            if (bid.get("town") or "").casefold() != (notice.get("town") or "").casefold():
                continue
            score = _score(bid, notice)
            if score > 0:
                candidates.append((score, bid_index, notice_index))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    used_bids: set[int] = set()
    used_notices: set[int] = set()
    for _score_value, bid_index, notice_index in candidates:
        if bid_index in used_bids or notice_index in used_notices:
            continue
        used_bids.add(bid_index)
        used_notices.add(notice_index)
        notice = notices[notice_index]
        bids[bid_index]["legal_notice_url"] = notice["url"]
        bids[bid_index]["paper"] = notice.get("paper")
    for bid_index, bid in enumerate(bids):
        source = bid.get("source") or "town"
        if source in ("town", "commbuys") and bid_index not in used_bids:
            bid["legal_notice_url"] = None
            bid["paper"] = None
    return [notice for index, notice in enumerate(notices) if index not in used_notices]


def _score(bid: dict, notice: dict) -> float:
    number = (bid.get("bid_number") or "").strip()
    blob = notice.get("title") or ""
    if number and len(number) >= 3:
        pattern = rf"(?<![A-Za-z0-9]){re.escape(number)}(?![A-Za-z0-9])"
        if re.search(pattern, blob, re.IGNORECASE):
            return 100.0
    title_tokens = _tokens(bid.get("title") or "")
    notice_tokens = _tokens(blob)
    if len(title_tokens) < 2 or not notice_tokens:
        return 0.0
    overlap = set(title_tokens) & set(notice_tokens)
    if len(overlap) < 2:
        return 0.0
    ratio = len(overlap) / len(set(title_tokens))
    if ratio < 0.5 and len(overlap) < 3:
        return 0.0
    return ratio * 10 + len(overlap)


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [word for word in words if word not in _STOP and len(word) > 2]


def _primary_town(text: str, town_regions: dict[str, str]) -> str | None:
    found = []
    for town in sorted(town_regions, key=len, reverse=True):
        if re.search(rf"(?<![A-Za-z]){re.escape(town)}(?![A-Za-z])", text, re.IGNORECASE):
            found.append(town)
    if not found:
        return None
    for town in found:
        if re.search(rf"(?<![A-Za-z])(?:town|city) of {re.escape(town)}(?![A-Za-z])", text, re.IGNORECASE):
            return town
    return found[0]


def _fields(html: str, phrase: str = "", mode: str = "AND", submit: bool = False) -> dict[str, str]:
    fields = {
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": _input(html, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": _input(html, "__VIEWSTATEGENERATOR"),
        "ctl00_ToolkitScriptManager1_HiddenField": "",
        f"{_PREFIX}txtSearch": phrase,
        f"{_PREFIX}rdoType": mode,
        f"{_PREFIX}txtExclude": "",
        f"{_PREFIX}hdnLastScrollPos": "0",
        f"{_PREFIX}hdnCountyScrollPosition": "-1",
        f"{_PREFIX}hdnCityScrollPosition": "-1",
        f"{_PREFIX}hdnPubScrollPosition": "-1",
        f"{_PREFIX}hdnField": "",
        f"{_PREFIX}dateRange": "rbLastNumDays",
        f"{_PREFIX}txtLastNumDays": "14",
        f"{_PREFIX}txtLastNumWeeks": "52",
        f"{_PREFIX}txtLastNumMonths": "12",
        f"{_PREFIX}txtDateFrom": _input(html, f"{_PREFIX}txtDateFrom"),
        f"{_PREFIX}txtDateTo": _input(html, f"{_PREFIX}txtDateTo"),
        f"{_PREFIX}txtSSID": "0",
        "ctl00$ContentPlaceHolder1$WSExtendedGridNP1$GridView1$ctl01$ddlPerPage": "10",
    }
    for index in _COUNTIES:
        fields[f"{_PREFIX}lstCounty${index}"] = "on"
    if submit:
        fields[f"{_PREFIX}btnGo"] = ""
    if not fields["__VIEWSTATE"]:
        raise LegalNoticeError("legal-notice form had no view state")
    return fields


def _page_count(html: str) -> int:
    match = _PAGES.search(html)
    if not match:
        return 1
    return max(1, int(match.group(1)))


def _input(html: str, name: str) -> str:
    match = re.search(rf'<input[^>]*name="{re.escape(name)}"[^>]*>', html)
    if not match:
        return ""
    value = re.search(r'value="([^"]*)"', match.group(0))
    return value.group(1) if value else ""


def _body_text(block: str) -> str:
    cell = re.search(r'colspan="3"[^>]*>([\s\S]*?)</td>', block, re.IGNORECASE)
    raw = cell.group(1) if cell else block
    text = _text(raw)
    text = re.sub(r"(?i)click 'view' to open the full text\.?", "", text).strip()
    return re.sub(r"\s+", " ", text).strip(" .")


def _text(value: str) -> str:
    cleaned = _TAG.sub(" ", value or "")
    return re.sub(r"\s+", " ", unescape(cleaned).replace("\xa0", " ")).strip()


def _reject_challenge(html: str, url: str) -> None:
    match = re.search(r"(?is)<title>(.*?)</title>", html or "")
    title = _text(match.group(1)).lower() if match else ""
    if "captcha" in title or "just a moment" in title or "attention required" in title:
        raise LegalNoticeError(f"{url} asked for a human check; not attempted")
