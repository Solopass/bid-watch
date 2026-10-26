# bid-watch

A personal tracker for open municipal bids and RFPs in MetroWest Massachusetts. MetroWest core is Framingham, Natick, Marlborough, Hudson, Westborough, Ashland, Hopkinton, Sudbury, Wayland, Southborough, Holliston, and Milford. The next ring is Wellesley, Weston, Needham, Shrewsbury, Stow, Acton, Concord, Dover, Sherborn, Bellingham, Upton, Grafton, Maynard, Lincoln, and Mendon.

A GitHub Actions workflow reads each town's public bid page once a day and writes `data/bids.json`. This repository's GitHub Pages site is a static dashboard of that file. There is no server, no paid API, and no API key.

The dashboard is [https://solopass.github.io/bid-watch/](https://solopass.github.io/bid-watch/).

## What it collects

For each posting: town, title, bid number when the page has one, posted date when the page has one, due date when the page has one, category when the page has one, and the public URL. `first_seen` is the day the bid first appeared. A bid is marked closed when its due date has passed, or when a successful scrape of its listing no longer shows it. A town that fails does not close that town's existing bids.

`data/status.json` records each town as `ok` or `error`, plus the time of the last successful scrape. The dashboard shows that as **Last updated** in America/New_York.

## Turn on GitHub Pages

The site is the files at the root of `main` (`index.html` and `data/`). GitHub does not publish them until Pages is enabled:

1. Open the repository **Settings → Pages**.
2. Under **Build and deployment**, set **Source** to **Deploy from a branch**.
3. Set the branch to **main** and the folder to **/ (root)**. Save.
4. Wait for the Pages deployment. The site is [https://solopass.github.io/bid-watch/](https://solopass.github.io/bid-watch/).

## Turn on Actions

The scraper is [`.github/workflows/scrape.yml`](.github/workflows/scrape.yml). It runs on a schedule and when someone starts it by hand. It does not run on push or on a pull request.

1. Open the **Actions** tab. If GitHub asks you to enable workflows, allow them.
2. Open **Settings → Actions → General**. Under **Workflow permissions**, choose **Read and write permissions**. The daily job commits `data/bids.json` and `data/status.json` only when those files change, and it opens a GitHub issue when it finds new open bids. Both need the workflow token to write. No other secret is used.
3. The schedule is `0 11 * * *` UTC, which is 7:00am Eastern Daylight Time and 6:00am Eastern Standard Time. GitHub may start a scheduled run later than that. It still runs once a day.

**Refresh now** on the dashboard opens the workflow page:

[https://github.com/Solopass/bid-watch/actions/workflows/scrape.yml](https://github.com/Solopass/bid-watch/actions/workflows/scrape.yml)

Choose **Run workflow**. That is the manual run. It does not add a second schedule.

When a run finds new open bids, it opens (or comments on) a GitHub issue titled `New bids YYYY-MM-DD`. The issue lists each bid as a link with its due date, so the repository owner gets GitHub's notification email. No issue is opened when nothing is new.

## How a town is read

`sources.json` lists every town. Parsers are per page type, not per town:

| `type` | Pages |
| --- | --- |
| `civicplus` | CivicPlus `Bids.aspx`. The original MetroWest pages, Westborough's usually empty town page, and the next-ring towns (Wellesley, Weston, Needham, Shrewsbury, Stow, Acton, Concord, Dover, Sherborn, Bellingham, Upton, Grafton, Maynard, Lincoln, Mendon). Each next-ring page is the town's official `Bids.aspx` and uses the same list markup. |
| `bidnet` | BidNet Direct agency HTML (Westborough, Hopkinton) |
| `wordpress` | Sudbury's post list. The list has no due date, so each new post is opened. |
| `documents` | Holliston's procurement page. Due dates are in the headings; the first PDF link is the bid. |

Natick leaves years of award notices up as "Open / Upon Contract". Those rows are skipped. A real future close with no bid number is read from the detail page. Southborough "Upon Contract" rows are opened so a due date buried in the description can close a stale posting.

Hopkinton's town site links to Beacon (`beaconbid.com`). That site is a JavaScript app behind a captcha, so it is not scraped. The Hopkinton source is the town's public BidNet list instead.

Most Massachusetts construction bids over $10,000 are also posted on COMMBUYS. The run downloads the public open-solicitation CSV from the COMMBUYS search page (one page view, then the CSV export on that same page). It does not keep the statewide list. A row is kept when the organization is a covered town or city, that town's public schools, housing authority, water district, or community development authority, or a regional school district that serves a covered town (Keefe Tech, Assabet Valley, Lincoln-Sudbury, Algonquin / Northborough-Southborough, Nashoba, Tri-County, Blackstone Valley Tech, Minuteman). The public CSV has no location column. If a location column shows up later, it counts only when the whole value is one covered town, not when a description mentions the name. Framingham State University and other similar names are not covered towns.

When a COMMBUYS row matches a bid already collected from a town page, that bid is marked `on_commbuys: yes` and the row is not copied. A covered row with no town-page match is stored as its own bid with `source: commbuys`. Other town bids are `on_commbuys: no`. If the export fails, existing flags stay and no new COMMBUYS rows are added. The check does not log in and does not try to get around a challenge.

A regional district is grouped with MetroWest core when a core town belongs to it (Assabet Valley, Keefe Tech, Lincoln-Sudbury, Algonquin, Blackstone Valley Tech) and with the next ring otherwise (Nashoba, Tri-County, Minuteman).

The Massachusetts Central Register is not scraped. The weekly bulletin PDF is archived by the State Library of Massachusetts, and the collection page at `archives.lib.state.ma.us` is allowed, but the list of issues is the items tab (`/entities/.../items`), which `robots.txt` disallows. Discovery search is disallowed too. The public OAI endpoint answers `Identify` and then returns no records (`ListSets` is HTTP 500). The collection RSS URL 404s. The sitemaps are allowed, but they are untitled entity URLs, about 100MB, and not a feed of the latest bulletin. `sec.state.ma.us` is behind a challenge and is not requested. The dashboard still has a Central Register source filter; it stays empty, and the health strip says skipped.

## Add a town

Add an object to the `sources` array in `sources.json`:

```json
{
  "town": "Sherborn",
  "region": "Next ring",
  "type": "civicplus",
  "url": "https://www.sherbornma.org/Bids.aspx",
  "notes": "Why this page is the public list."
}
```

`region` is `MetroWest core` or `Next ring`. The dashboard groups towns by that field.

Use `bidnet`, `wordpress`, or `documents` when the page matches those shapes. CivicPlus options:

- `when_contract`: `skip` (default) or `detail` for "Upon Contract" rows.
- `when_blank`: `detail` (default) or `skip` when the close line is empty.
- `detail_if_missing_number`: open the detail page when a dated row has no bid number.

Run it locally with Python 3.11 or newer. The standard library is enough:

```bash
python -m scraper.scrape
python -m unittest discover -s tests
```

A full run takes several minutes because each host is contacted at least 10 seconds apart, and longer when that host's `robots.txt` asks for a slower crawl.

## Politeness

The scraper sends `bid-watch/1.0 (personal municipal bid tracker; +https://github.com/solopass/bid-watch)`, reads `robots.txt` before any other URL on that host, and waits at least 10 seconds between requests to the same host. It makes one pass per day unless you start the workflow by hand. It does not log in, does not submit a captcha, and does not read pages `robots.txt` disallows.

This is for personal use. It only reads public pages.
