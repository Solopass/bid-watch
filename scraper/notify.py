"""Open or comment on a GitHub issue when a run found new open bids.

No issue is created when the new-bid list is empty. The workflow's
GITHUB_TOKEN is the only credential, and it stays in the Actions environment.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.dates import format_due


def main() -> int:
    path = ROOT / "data" / "new_bids.json"
    if not path.exists():
        print("No new-bid file; nothing to notify.")
        return 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    bids = payload.get("bids") or []
    if not bids:
        print("No new bids; no issue opened.")
        return 0
    date = payload.get("date") or "today"
    title = f"New bids {date}"
    body = _body(date, bids)
    body_path = ROOT / "data" / "new_bids_issue.md"
    body_path.write_text(body, encoding="utf-8")
    if not os.environ.get("GH_TOKEN") and not os.environ.get("GITHUB_TOKEN"):
        print("GH_TOKEN is not set; wrote the issue body and skipped GitHub.")
        return 0
    existing = _find_issue(title)
    if existing is None:
        subprocess.run(
            ["gh", "issue", "create", "--title", title, "--body-file", str(body_path)],
            check=True,
        )
        print(f"Opened issue {title}")
        return 0
    number = str(existing["number"])
    if existing.get("state", "").upper() == "CLOSED":
        subprocess.run(["gh", "issue", "reopen", number], check=True)
    subprocess.run(
        ["gh", "issue", "comment", number, "--body-file", str(body_path)],
        check=True,
    )
    print(f"Commented on issue #{number}")
    return 0


def _body(date: str, bids: list[dict]) -> str:
    lines = [
        f"New open bids found on {date}.",
        "",
        "Personal-use tracker. Each link is a public posting.",
        "",
    ]
    labels = {
        "town": "Town page",
        "commbuys": "COMMBUYS",
        "central_register": "Central Register",
    }
    for bid in bids:
        number = f" ({bid['bid_number']})" if bid.get("bid_number") else ""
        origin = labels.get(bid.get("source") or "town", bid.get("source") or "Town page")
        lines.append(
            f"- **{bid['town']}** ({origin}) — [{bid['title']}]({bid['url']}){number} — due {format_due(bid.get('due_date'))}"
        )
    lines.append("")
    return "\n".join(lines)


def _find_issue(title: str) -> dict | None:
    result = subprocess.run(
        [
            "gh",
            "issue",
            "list",
            "--state",
            "all",
            "--limit",
            "50",
            "--search",
            f'in:title "{title}"',
            "--json",
            "number,title,state",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    for issue in json.loads(result.stdout or "[]"):
        if issue.get("title") == title:
            return issue
    return None


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or exc, file=sys.stderr)
        raise SystemExit(1)
