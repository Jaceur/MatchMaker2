# New Incorps → Google Sheet (one-way feed)

The Sheet is now the AE-facing surface for new incorporations. Companies House
streams into CHStream, CHStream POSTs into the Matchmaker API, and the API pushes
each company into this Sheet.

```
Companies House stream
        │
        ▼
    CHStream  (Railway)              — unchanged, no edits needed
        │  POST /new-incorps/ingest
        ▼
 Matchmaker API (Railway)            — decides HIGH VALUE, archives it in Postgres
        │  POST  (Apps Script web app, batched)
        ▼
   Apps Script  ──writes──▶  the Google Sheet
```

**The data only ever flows down this page.** The API makes outbound POSTs; the
Apps Script has no `doGet`, so the published URL cannot be used to read the
sheet; and Matchmaker holds no Google credentials at all. Nothing typed in the
Sheet can reach the app — the "Claimed by / Status / Notes" columns are yours
alone, and the script will never overwrite them.

> On terminology: this is a **webhook**, not a WebSocket. Google Sheets can't
> accept a WebSocket, and a WebSocket is two-way by nature — a POST-only webhook
> is what actually enforces "one way".

---

## What you need to do — 10 minutes, once

**1. Make the spreadsheet.** A new Google Sheet, named whatever you like. You do
not need to create tabs or headers — the script creates **New Incorps** and
**High Value** with their headers the first time data arrives.

**2. Open the script editor.** In the Sheet: **Extensions → Apps Script**. Delete
whatever is in `Code.gs` and paste in the contents of [`Code.gs`](Code.gs) from
this folder.

**3. Set the shared key.** At the top of the script, replace
`PASTE_THE_SAME_LONG_RANDOM_STRING_HERE` with a long random string — anything
40+ characters. Keep a copy; you need it again in step 6. Save (💾).

**4. Test it before wiring anything up.** In the editor's toolbar pick the
function **`testWrite`** and press **Run**. Google will ask you to authorise the
script the first time ("Review permissions" → your account → *Advanced* → *Go to
(project name)* → *Allow*) — that's the script asking for permission to edit
**your own** sheet. It should create both tabs with one `TEST ROW LTD` row in
each. Delete those two rows.

**5. Deploy it as a web app.** **Deploy → New deployment** → the gear icon →
**Web app**, then:

| Field | Value |
|---|---|
| Description | `Matchmaker incorps feed` |
| Execute as | **Me** |
| Who has access | **Anyone** |

Press **Deploy** and copy the **Web app URL** — it ends in `/exec`.

> "Anyone" sounds alarming but is required: the Railway API isn't a signed-in
> Google user. The URL is unguessable and the shared key is checked on every
> request, and there's no read path — the worst case is someone who has both the
> URL and the key writing junk rows.

**6. Give the API the URL and key.** On the Railway **MatchMaker2** (API)
service → Variables, add:

| Variable | Value |
|---|---|
| `SHEET_WEBHOOK_URL` | the `/exec` URL from step 5 |
| `SHEET_WEBHOOK_KEY` | the same long random string from step 3 |
| `SHEET_SEND_ALL` | `1` for every incorporation (~2,000/day), `0` for high-value only |

The service redeploys; on boot the logs say
`[sheet] sink started (all + high-value) -> https://script.google.com/...`.

**7. Check it's flowing.** Either watch the Sheet (new rows land at the **top**,
under the header), or hit `GET /new-incorps/sheet-status` while logged in — it
returns `queued / sent / dropped / last_sent_at / last_error`.

---

## Living with it

- **New rows appear at the top**, not the bottom. New incorporations are a race,
  so the freshest is always row 2. Sorting or filtering the sheet yourself is
  fine — the script inserts under the header regardless.
- **Two tabs.** *New Incorps* is everything; *High Value* is the filtered feed
  (capital > £25k, a corporate owner, or a London Zone-1 postcode). A high-value
  company appears in both, and the **Why high value** column says which rule
  fired.
- **Timing.** High-value rows are pushed within ~3 seconds. Ordinary rows are
  batched for up to 60 seconds — this is deliberate: it keeps the script inside
  Google's daily runtime quota, and nobody is racing on the unfiltered feed.
- **Add your own columns freely** (Claimed by, Status, Notes, anything). The
  script matches on header *name* and only fills columns Matchmaker sent, so
  yours are never touched. You can also reorder or delete our columns — the
  header row wins. The one column to leave alone is **Company number**: it's how
  duplicates are detected.
- **Duplicates.** The script checks the newest 2,000 rows before inserting, so a
  Companies House re-send or an API restart won't double up.
- **Volume.** ~2,000 rows a day on *New Incorps*. Google Sheets tops out at 10
  million cells, so with ~22 columns you have roughly a year before it needs
  archiving — copy the old rows to a second spreadsheet once a quarter and delete
  them.
- **If rows stop arriving:** check `/new-incorps/sheet-status` first.
  `last_error: "bad key"` means step 3 and step 6 don't match. A `401` in
  CHStream's logs is a different (earlier) link in the chain — see HANDOVER §13.
- **Redeploying the script:** after editing `Code.gs` you must **Deploy → Manage
  deployments → ✏️ → Version: New version → Deploy**, or the live URL keeps
  running the old code. The URL itself stays the same.

## For whoever changes this next

- `Code.gs` has a test harness that fakes the Sheets API, so you can change it
  without a live spreadsheet: `node google_sheet/test_code_gs.js` from the
  project root. It covers the important one — an AE's typed columns surviving a
  row insert.
- The Python half is `api/sheet_sink.py` (+ `tests/test_sheet_sink.py`):
  batching, the two latency tiers, and the rule that a broken sheet can never
  break ingest.
- `python sheet_backfill.py 30` replays the last 30 days of archived high-value
  incorps into the sheet — useful the first time, and safe to re-run.

## The DB copy is still there

High-value incorps are still written to Postgres (`high_value_incorps`) and the
claim/pipeline tables still exist, so nothing is lost if you want the in-app view
back later. The Sheet is an additional destination, not a replacement for the
archive.
