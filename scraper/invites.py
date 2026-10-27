"""Turn parsed plan-room invites into dashboard rows.

The public site does not receive these rows. ``data/bids.json`` is what
GitHub Pages publishes. This writer only creates ``data/invites.local.json``,
which is gitignored, and the dashboard loads that file when it is present
on the machine serving the page.

Gmail parsing lives in ``apps-script/Parse.gs``. This module applies the
same region, state, and cladding rules as the rest of Bid Watch.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from scraper.dates import EASTERN, parse_when, today_eastern
from scraper.places import town_regions_from_config
from scraper.trades import cladding_trade

ROOT = Path(__file__).resolve().parents[1]
LOCAL_OUT = ROOT / "data" / "invites.local.json"
PUBLIC_BIDS = ROOT / "data" / "bids.json"

STATE_BY_REGION = {
    "RI border": "Rhode Island",
    "NH border": "New Hampshire",
    "CT border": "Connecticut",
}
_STREET = (
    r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|"
    r"place|pl|way|circle|cir|court|ct)"
)


def parse_fixture_dir(folder: Path) -> list[dict]:
    """Run the Apps Script parser on synthetic .eml fixtures."""
    script = ROOT / "apps-script" / "parse_cli.js"
    raw = subprocess.check_output(
        ["node", str(script), str(folder)],
        cwd=str(ROOT),
        text=True,
    )
    return json.loads(raw)


def rows_from_sheet_csv(path: Path) -> list[dict]:
    """Rows from a private Sheet download. Columns match Code.gs HEADERS."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        parsed = []
        for row in reader:
            parsed.append(
                {
                    "messageId": (row.get("Message ID") or "").strip(),
                    "project": (row.get("Project") or "").strip(),
                    "gc": (row.get("GC") or "").strip(),
                    "dueText": (row.get("Due") or "").strip(),
                    "location": (row.get("Location") or "").strip(),
                    "scope": (row.get("Scope") or "").strip(),
                    "link": (row.get("Link") or "").strip(),
                    "subject": (row.get("Subject") or "").strip(),
                    "parseStatus": (row.get("Status") or "").strip() or "parsed",
                }
            )
    return parsed


def to_dashboard_bids(parsed_rows: list[dict], town_regions: dict[str, str] | None = None) -> list[dict]:
    regions = town_regions or town_regions_from_config()
    today = today_eastern(datetime.now(EASTERN))
    bids = []
    seen = set()
    for item in parsed_rows:
        message_id = (item.get("messageId") or "").strip()
        if message_id and message_id in seen:
            continue
        if message_id:
            seen.add(message_id)
        bids.append(_one(item, regions, today))
    return bids


def write_local(bids: list[dict], path: Path = LOCAL_OUT) -> None:
    target = path.resolve()
    if target == PUBLIC_BIDS.resolve():
        raise SystemExit(
            "Refusing to write invite rows into data/bids.json. That file is published by GitHub Pages."
        )
    payload = {
        "private": True,
        "note": "Local only. Do not commit. These invites are not on the public site.",
        "updated": datetime.now(EASTERN).isoformat(timespec="seconds"),
        "bids": bids,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _one(item: dict, town_regions: dict[str, str], today: str) -> dict:
    title = (item.get("project") or item.get("subject") or "Plan room invite").strip()
    scope = (item.get("scope") or "").strip()
    gc = (item.get("gc") or "").strip()
    location = (item.get("location") or "").strip()
    town = town_mentioned(location, town_regions) or ""
    region = town_regions.get(town, "") if town else ""
    category = " — ".join(part for part in (gc, scope) if part) or None
    return {
        "town": town or location,
        "region": region,
        "sector": "private",
        "state": state_for(region, location),
        "source": "plan_room",
        "kind": "open",
        "title": title,
        "bid_number": None,
        "category": category,
        "trade": cladding_trade(title, scope, category),
        "posted_date": None,
        "due_date": parse_when(item.get("dueText")),
        "url": (item.get("link") or "").strip(),
        "source_url": "",
        "on_commbuys": "no",
        "commbuys_url": None,
        "paper": None,
        "legal_notice_url": None,
        "status": "open",
        "first_seen": today,
        "last_seen": today,
        "closed_on": None,
    }


def town_mentioned(text: str, town_regions: dict[str, str]) -> str | None:
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (text or "").casefold())
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(rf"\b[a-z]+\s+{_STREET}\b", " ", cleaned)
    for town in sorted(town_regions, key=len, reverse=True):
        name = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", town.casefold())).strip()
        if name and re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", cleaned):
            return town
    return None


def state_for(region: str, location: str) -> str:
    if region in STATE_BY_REGION:
        return STATE_BY_REGION[region]
    blob = location or ""
    if re.search(r"(?i)\brhode island\b|(?<![a-z0-9])ri(?![a-z0-9])", blob):
        return "Rhode Island"
    if re.search(r"(?i)\bnew hampshire\b|(?<![a-z0-9])nh(?![a-z0-9])", blob):
        return "New Hampshire"
    if re.search(r"(?i)\bconnecticut\b|(?<![a-z0-9])ct(?![a-z0-9])", blob):
        return "Connecticut"
    if region or re.search(r"(?i)\bmassachusetts\b|(?<![a-z0-9])ma(?![a-z0-9])", blob):
        return "Massachusetts"
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a local-only invite file for the dashboard.")
    parser.add_argument("--fixtures", type=Path, help="Directory of synthetic .eml files")
    parser.add_argument("--csv", type=Path, help="CSV exported from the private Google Sheet")
    parser.add_argument("--out", type=Path, default=LOCAL_OUT)
    args = parser.parse_args(argv)
    if not args.fixtures and not args.csv:
        parser.error("Pass --fixtures or --csv")
    parsed = []
    if args.fixtures:
        parsed.extend(parse_fixture_dir(args.fixtures))
    if args.csv:
        parsed.extend(rows_from_sheet_csv(args.csv))
    write_local(to_dashboard_bids(parsed), args.out)
    print(f"Wrote {len(parsed)} local invite row(s) to {args.out}")
    print("This file is gitignored. Do not commit it. It is not uploaded to GitHub Pages.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
