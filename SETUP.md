# Plan-room invites

Solopass's Gmail receives BuildingConnected, PlanHub, and other bid invitations. A Google Apps Script in that Gmail account copies the useful fields into a **private Google Sheet**. Nothing in this repository logs into a plan room. The daily GitHub Action does not read Gmail.

The public dashboard at `https://solopass.github.io/bid-watch/` cannot show a row to only one person. If an invite is committed into `data/bids.json`, GitHub Pages publishes the project name, the GC, the due date, and the plan-room link to anyone on the internet. Making the repository or Pages private requires a paid GitHub plan. The default is the private Sheet. Do not publish the Sheet (File → Share → Publish to web stays off).

## Do this once

1. In Gmail, open **Settings → See all settings → Filters and Blocked Addresses → Create a new filter**. In **Has the words**, paste:

   `from:buildingconnected.com OR from:planhub.com OR subject:"invitation to bid" OR subject:"bid invite" OR subject:ITB`

   Continue, check **Apply the label**, choose **New label**, and name it `bid-invites`. Create the filter.

2. On your computer, open the `apps-script` folder in this repository. You will paste two files in the next step.

3. Go to [script.google.com](https://script.google.com) while signed in to the Gmail account that receives the invites. **New project**. Name it `Bid Watch invites`.

4. Select the starter function in `Code.gs` and replace the whole file with the contents of `apps-script/Code.gs`. Click **+** next to Files, choose **Script**, and name it `Parse`. Replace its contents with `apps-script/Parse.gs`.

5. Click the gear (**Project Settings**). Turn on **Show "appsscript.json" manifest file in editor**. Open that file and replace it with `apps-script/appsscript.json`. Save.

6. In the editor, choose the function **`setup`** in the toolbar and click **Run**. Google asks for permission to read and label Gmail and to create a spreadsheet. Allow it. There is no key to copy into GitHub. `setup` stores the spreadsheet id in the script's own properties and installs an hourly trigger.

7. Open **Executions**, open the `setup` log, and click the spreadsheet link. That Sheet is the invite list. It stays in this Google account. The script adds a label `bid-invites-processed` after it records a message, and it skips a message id it has already stored. A message it cannot parse still gets a row: the subject as the title, the link when there is one, and the other fields blank.

8. The clock icon (**Triggers**) should show `processInvites` every hour. Leave it.

## See invites on the dashboard, only on your computer

Skip this if the Sheet is enough.

1. In the Sheet, **File → Download → Comma Separated Values (.csv)**.
2. From the repository folder, run:

   `python3 -m scraper.invites --csv ~/Downloads/Invites.csv`

3. That writes `data/invites.local.json`. The file is listed in `.gitignore`. Open the dashboard from a local web server (not the public Pages URL). A note at the top says the private rows are local. Sector **Private** and source **Plan room invite** show them. The cladding tag uses the project name and the scope text.

Do not commit `data/invites.local.json` or paste the Sheet into `data/bids.json`.
