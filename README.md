# bid-watch

A personal tracker for open municipal bids and RFPs around MetroWest Massachusetts, plus nearby Rhode Island and New Hampshire towns. MetroWest core is Framingham, Natick, Marlborough, Hudson, Westborough, Ashland, Hopkinton, Sudbury, Wayland, Southborough, Holliston, and Milford. The next ring adds Medway to Wellesley, Weston, Needham, Shrewsbury, Stow, Acton, Concord, Dover, Sherborn, Bellingham, Upton, Grafton, Maynard, Lincoln, and Mendon. The outer ring is the next band of towns with a public CivicPlus bid list, roughly an hour from Framingham. RI border and NH border are the CivicPlus lists in northern Rhode Island and southern New Hampshire.

A GitHub Actions workflow reads each town's public bid page once a day and writes `data/bids.json`. This repository's GitHub Pages site is a static dashboard of that file. There is no server, no paid API, and no API key.

Plan-room invitations that arrive in Gmail stay in a private Google Sheet. [SETUP.md](SETUP.md) is the one-time setup. Those invites are not written into `data/bids.json`, because this Pages site is public.

The dashboard is [https://solopass.github.io/bid-watch/](https://solopass.github.io/bid-watch/).

## What it collects

For each posting: town, region, state, sector, source, kind (`open` or `upcoming`), title, bid number when the page has one, posted date when the page has one, due date when the page has one, category when the page has one, and the public URL. `first_seen` is the day the bid first appeared. A bid is marked closed when its due date has passed, or when a successful scrape of its listing no longer shows it. A town or feed that fails does not close the bids it previously found. Region is `MetroWest core`, `Next ring`, `Outer ring`, `RI border`, `NH border`, `CT border`, or `Statewide agency`. Sector is `town`, `school`, `housing`, `agency`, or `utility`, so a school or housing-authority bid can be filtered without hiding the town. State is Massachusetts unless the row is a border town.

Different hosts are fetched together, up to 8 at a time. The wait between requests to the same host stays at least 10 seconds.

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

Legal notices come from the Massachusetts Newspaper Publishers Association search at masspublicnotices.org. The run asks for the last 14 days in Middlesex, Norfolk, and Worcester, using bid, RFP, and RFQ phrases. A notice is kept only when the listing names one of the 27 towns. The full notice text is behind a captcha, so the scraper stores the listing snippet, the paper, the publish date, a due date when that snippet states one, and the detail link. It does not open the detail page. When the snippet matches a town-page or COMMBUYS bid for the same town (same bid number, or a fuzzy title match), that existing row is flagged with the notice link instead of being copied.

Bid Express is one HTML-table parser for two public boards: DCAMM and Massport (the Massport board includes Worcester airport when those solicitations are listed). Only the upcoming table is read. Deadlines are converted from UTC to US Eastern. MassDevelopment's RFP page is a heading followed by a PDF, including Devens projects. The MBTA current-solicitation lists are kept in full, including Boston work, and tagged `region: Statewide agency`. A handful of public detail pages are opened for the due date. MBTA future projects and the MSBA estimated-bid schedule are `kind: upcoming`, so the dashboard's Upcoming projects tab shows them apart from open bids. MSBA rows are limited to covered towns. The capital-planning page is checked daily, and the PDF is downloaded only when its link has changed.

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

`region` is `MetroWest core`, `Next ring`, `Outer ring`, `RI border`, `NH border`, or `CT border`. Optional `sector` is `town` (the default), `school`, `housing`, `college`, `hospital`, `agency`, `utility`, or `private`. Optional `state` defaults to Massachusetts. Statewide boards live in `feeds`, not in this town list. The dashboard groups towns by region and adds `Statewide agency` for DCAMM, Massport, MassDevelopment, the MBTA, MassDOT, and DCR. A row is tagged Cladding when the title or category names siding, facade, curtain wall, storefront, or a related exterior-envelope term. The Trade filter shows only those rows.

Use `bidnet`, `wordpress`, `documents`, or `notices` when the page matches those shapes. `notices` is a plain HTML list of bid documents or a heading with a bid-opening date. CivicPlus options:

- `when_contract`: `skip` (default) or `detail` for "Upon Contract" rows.
- `when_blank`: `detail` (default) or `skip` when the close line is empty.
- `detail_if_missing_number`: open the detail page when a dated row has no bid number.

## Pages that were checked and not added

These were public-page or robots problems, or there was no bid list to parse. COMMBUYS still covers a Massachusetts organization when its name matches a covered town, a regional district already in the matcher (Dover-Sherborn, Acton-Boxborough, Mendon-Upton, Nashoba, Tri-County, and the vocational districts), or an in-range college or regional agency (Framingham State, MassBay, Worcester State, MWRA, MWRTA, MAPC, CMRPC).

- Millis, Northborough, Dedham, Westwood, Newton, Woonsocket, Burrillville, Smithfield, Thompson, and Putnam: the bid URL answered with a human check or an access denial.
- Hopedale, Foxborough, Walpole, Norwood, Sharon, Boylston, Bolton's Bids.aspx, Woodstock, Killingly, and Pawtucket: no CivicPlus bid module at the public URL.
- Berlin: procurement page is behind a captcha.
- Nashoba Regional, Natick Public Schools, and Marlborough Public Schools: the bid page is a client challenge. Nashoba stays on COMMBUYS.
- Dover-Sherborn, Acton-Boxborough, and Mendon-Upton: no separate public bid list. Their COMMBUYS organization names are matched.
- Tri-County and Hopkinton Public Schools: no working public bid page.
- Hudson Public Schools facilities page: no bid documents.
- Providence purchasing and the Rhode Island OSP bid board: the public page is a landing page, not a filterable solicitation list. RIVIP's external search is a form with no open list.
- New Hampshire Purchasing: the site returned access denied.
- Connecticut CTsource: the public page links to guides. The bid board itself is not an open HTML list.
- Central Register: still skipped. Its bulletin list is disallowed by robots.txt.
- MWRA Supplier Portal: the public mwra.com page has no bid table. New bids are posted on an Infor CloudSuite site that requires registration. MWRA rows on COMMBUYS are kept. The portal itself is not opened.
- MWRTA: `/procurement` and `/doing-business` return 404, and the homepage is a transit schedule, not a bid list. COMMBUYS organization matches are kept.
- CMRPC: `/bids/` is 404. The procurement page describes accounting, auctioneer, and fuel programs, not an open solicitation list. News posts for past RFPs are closed archives. COMMBUYS organization matches are kept.
- Mass Maritime, UMass Amherst, UMass Boston, UMass Lowell, and UMass Dartmouth: outside the roughly one-hour ring.
- UMass Chan Medical School procurement page: the public URL answered with a human check and was not opened.
- Framingham State, Worcester State, and MassBay: the guessed procurement HTML paths are not bid lists (Framingham's offices page has no bid link; the others 404). Their COMMBUYS organization names are matched. Framingham State's 2025 bid-log PDF is an archive, not a live list.
- Hospitals: MetroWest Medical Center's hostname does not resolve. Milford Regional's public page is a hospital site with no bid list. No other hospital system published an open bid list without a login.
- Private commercial cladding listings: BuildingConnected, Dodge, ConstructConnect, and PlanHub require an account and were not opened. BidDocs (biddocs.com) is a commercial plan room, not a public project list. No public, no-login GC or developer board of commercial exterior-cladding invitations was found, so nothing was added under sector `private`. The sector and the Cladding filter are on the dashboard for rows from the public sources.

Run it locally with Python 3.11 or newer. The standard library is enough:

```bash
python -m scraper.scrape
python -m unittest discover -s tests
```

A full run takes several minutes because each host is contacted at least 10 seconds apart, and longer when that host's `robots.txt` asks for a slower crawl.

## Politeness

The scraper sends `bid-watch/1.0 (personal municipal bid tracker; +https://github.com/solopass/bid-watch)`, reads `robots.txt` before any other URL on that host, and waits at least 10 seconds between requests to the same host. It makes one pass per day unless you start the workflow by hand. It does not log in, does not submit a captcha, and does not read pages `robots.txt` disallows.

This is for personal use. It only reads public pages.
