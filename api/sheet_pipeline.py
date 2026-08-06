"""One-way sync of an AE's classify pipeline into a Google Sheet tab.

Everything the `/pipeline` page shows for a lead awaiting a CRM status, laid out
as spreadsheet rows: the Companies House link, the domain for a Salesforce
Business Search, the LinkedIn/SalesNav search, the "why now" opener, each
director with their officer page, and the five email guesses — each one a link
that opens Mailmeteor to verify it.

**Still strictly one-way.** This pushes; it never reads. The Outcome dropdown and
Notes column are yours: picking "Net New" in the sheet does NOT classify the lead
in Matchmaker, and nothing you type here comes back. Classifying still happens in
the app.

Unlike the incorps feed (an append-only stream), a pipeline is a MUTABLE LIST —
leads join it when you approve them and leave it when you classify them. So this
sends the whole current pipeline on a timer in `sync` mode: the Apps Script
matches on `Row key`, updates the Matchmaker columns of rows that already exist
(leaving your columns untouched), adds the new ones on top, and marks departed
rows `Left` rather than deleting them — deleting would take your notes with it.

ONE ROW PER DIRECTOR, not per lead: the five email guesses are per person, and a
lead with three directors needs fifteen. The lead's own columns repeat down its
rows, which is what makes each row independently sortable and filterable.

Config (API service): `SHEET_PIPELINE_USERS` (comma-separated usernames; empty =
off), `SHEET_TAB_PIPELINE`, `SHEET_PIPELINE_SYNC_MINUTES`.
"""
import asyncio
from datetime import datetime, timezone
from urllib.parse import quote

from sqlalchemy import text

from database import engine
from directors import email_candidates, domain_from_url

from .config import settings
from .sheet_sink import post_payload

# Mirrors CRM_STATUS_OPTIONS in frontend/src/components/ClassifyCard.tsx. There
# is no server-side list to import — if you change them there, change them here.
# ("Won" is retired for GDPR; don't reinstate it.)
CRM_STATUS_OPTIONS = [
    "Net New",
    "Existing Lead - Unclaimed",
    "Existing Lead - Already Claimed",
    "Existing Account - Unclaimed",
    "Existing Account - Already Claimed",
    "Disqualified",
]

# Columns the AE owns: never written, only created. "Outcome" gets the dropdown.
AE_COLUMNS = ["Outcome", "Notes"]

PIPELINE_COLUMNS = [
    "Row key", "In pipeline", "Company", "Company number", "Companies House",
    "Fit score", "Business search", "LinkedIn search", "Why now",
    "Director", "Director CH page", "Other companies",
    "Email 1", "Email 2", "Email 3", "Email 4", "Email 5",
]
KEY_COLUMN = "Row key"
MAX_EMAILS = 5

_CH_COMPANY = "https://find-and-update.company-information.service.gov.uk/company/"
_SALESNAV = "https://www.linkedin.com/sales/search/company?keywords="
_MAILMETEOR = "https://mailmeteor.com/email-checker?email="

# The same query the /pipeline page runs (approved, no CRM status yet), for one
# named AE. Kept in sync with api/routers/pipeline.py::unclassified — note the
# NOT EXISTS is on `crm_status IS NOT NULL`, NOT on row existence: approves write
# a label row at swipe time, so testing existence would empty the list.
_PIPELINE_SQL = text("""
    SELECT sl.id, sl.crn, sl.company_name, sl.lead_score,
           COALESCE(sl.corrected_website_url, sl.website_url)   AS website_url,
           COALESCE(sl.corrected_linkedin_url, sl.linkedin_url) AS linkedin_url,
           sl.directors_info, sl.active_directors,
           sl.capital_raise_recent, sl.charge_recent,
           sl.last_capital_raise, sl.last_charge,
           sl.updated_at
    FROM sales_leads sl
    WHERE sl.assigned_ae_username = :username
      AND sl.status = 'approved'
      AND NOT EXISTS (
          SELECT 1 FROM ml_pipeline_analytics m
          WHERE m.lead_id = sl.id AND m.crm_status IS NOT NULL
      )
    ORDER BY sl.updated_at ASC, sl.id ASC
""")
# ^ ASC, deliberately, even though the page shows newest-first. The Apps Script's
# one convention is "rows arrive oldest-first, the last one ends up on top" (the
# feed is naturally chronological). Sending newest-first here would land the
# OLDEST lead at the top of the tab.


def _quote(value: str) -> str:
    """Escape a string for use inside a Sheets formula's double quotes."""
    return str(value or "").replace('"', '""')


def _urlquote(value: str) -> str:
    return quote(str(value or ""), safe="@.")


def _full_url(url) -> str:
    """A URL Sheets will actually open. Some stored websites have no scheme
    ('greatfashion.co.uk'), and HYPERLINK treats a schemeless string as a
    RELATIVE link — a dead cell rather than the company's site."""
    s = str(url or "").strip()
    if not s:
        return ""
    return s if "//" in s.split("?")[0][:10] else "https://" + s


def _hyperlink(url: str, label: str) -> str:
    """A cell that DISPLAYS `label` but opens `url` when clicked — so an email
    cell reads as the address (and copies as one) while still being the
    one-click route to Mailmeteor."""
    if not url or not label:
        return label or ""
    return f'=HYPERLINK("{_quote(url)}","{_quote(label)}")'


def _why_now(lead: dict) -> str:
    """The conversation opener, same two triggers the classify card shows."""
    bits = []
    if lead.get("capital_raise_recent"):
        when = lead.get("last_capital_raise")
        bits.append(f"Raised capital{f' ({str(when)[:10]})' if when else ''}")
    if lead.get("charge_recent"):
        when = lead.get("last_charge")
        bits.append(f"New borrowing{f' ({str(when)[:10]})' if when else ''}")
    return "; ".join(bits)


def _directors_of(lead: dict) -> list[dict]:
    """[{name, appointments, officer_url}] — the richer directors_info when the
    lead has it, else the plain names string for leads enriched before it
    existed. Mirrors routers/pipeline.py::email_candidates_for_lead."""
    info = lead.get("directors_info") or []
    if info:
        return [{"name": d.get("name"), "appointments": d.get("appointments"),
                 "officer_url": d.get("url")}
                for d in info if d.get("name")]
    return [{"name": n.strip(), "appointments": None, "officer_url": None}
            for n in (lead.get("active_directors") or "").split(",") if n.strip()]


def rows_for_lead(lead: dict) -> list[dict]:
    """One row per director (at least one row per lead, even with no directors —
    a lead you can't see is worse than a lead with an empty name)."""
    domain = domain_from_url(lead.get("website_url"))
    company = str(lead.get("company_name") or "")
    crn = str(lead.get("crn") or "")
    shared = {
        "In pipeline": "Yes",
        "Company": company,
        "Company number": crn,
        "Companies House": (_CH_COMPANY + crn) if crn else "",
        "Fit score": lead.get("lead_score") if lead.get("lead_score") is not None else "",
        # Displays the bare domain (what Salesforce's Business Search wants, and
        # what copying the cell gives you); clicking opens the site. `domain` is
        # the same value the app's Business Search button copies — one function,
        # so the sheet and the app can't drift apart.
        "Business search": _hyperlink(_full_url(lead.get("website_url")), domain),
        "LinkedIn search": (_SALESNAV + _urlquote(company)) if company else "",
        "Why now": _why_now(lead),
    }

    rows = []
    for director in _directors_of(lead) or [{"name": "", "appointments": None, "officer_url": None}]:
        name = str(director.get("name") or "")
        appointments = director.get("appointments")
        row = {
            **shared,
            # Stable across syncs: it's what the Apps Script matches on to update
            # a row in place instead of adding a duplicate.
            "Row key": f"{crn}|{name}".lower(),
            "Director": name,
            "Director CH page": str(director.get("officer_url") or ""),
            # The card shows "N other companies" — appointments includes this one.
            "Other companies": max(0, appointments - 1) if appointments is not None else "",
        }
        candidates = email_candidates(name, domain) if name and domain else []
        for i in range(MAX_EMAILS):
            email = candidates[i][1] if i < len(candidates) else ""
            row[f"Email {i + 1}"] = _hyperlink(_MAILMETEOR + _urlquote(email), email) if email else ""
        rows.append(row)
    return rows


def fetch_rows(username: str) -> list[dict]:
    """Every row for one AE's current pipeline, newest lead first."""
    with engine.connect() as conn:
        leads = [dict(r) for r in conn.execute(_PIPELINE_SQL, {"username": username})
                 .mappings().fetchall()]
    rows: list[dict] = []
    for lead in leads:
        rows.extend(rows_for_lead(lead))
    return rows


def tab_for(username: str, users: list[str]) -> str:
    """One AE gets the plain tab name; several get a tab each, suffixed."""
    if len(users) <= 1:
        return settings.sheet_tab_pipeline
    return f"{settings.sheet_tab_pipeline} - {username}"


class PipelineSync:
    """Pushes each configured AE's pipeline to its tab, on a timer."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = False
        self.stats = {"syncs": 0, "rows": 0, "failures": 0,
                      "last_sync_at": None, "last_error": None}

    @property
    def users(self) -> list[str]:
        return [u.strip() for u in settings.sheet_pipeline_users.split(",") if u.strip()]

    @property
    def enabled(self) -> bool:
        # Needs the same webhook config the feed does, plus at least one AE.
        return bool(settings.sheet_webhook_url and settings.sheet_webhook_key and self.users)

    def _payload(self, sheets: dict[str, list[dict]]) -> dict:
        return {
            "columns": PIPELINE_COLUMNS,
            "aeColumns": AE_COLUMNS,
            # sync = upsert by key + mark departed rows, instead of append.
            "mode": "sync",
            "keyColumn": KEY_COLUMN,
            # A row we stop sending has left the pipeline (you classified it, or
            # it was reassigned). Mark it, don't delete it — deleting would take
            # the Outcome and Notes you typed with it.
            "presence": {"column": "In pipeline", "absent": "Left"},
            "validation": {"Outcome": CRM_STATUS_OPTIONS},
            "sheets": sheets,
        }

    def sync_once(self) -> tuple[bool, str | None]:
        """Blocking: read every configured pipeline and push it. Worker thread."""
        users = self.users
        sheets = {tab_for(u, users): fetch_rows(u) for u in users}
        total = sum(len(r) for r in sheets.values())
        ok, error = post_payload(self._payload(sheets), label=f"pipeline sync ({total} rows)")
        if ok:
            self.stats["syncs"] += 1
            self.stats["rows"] = total
            self.stats["last_sync_at"] = datetime.now(timezone.utc).isoformat()
            self.stats["last_error"] = None
        else:
            self.stats["failures"] += 1
            self.stats["last_error"] = error
        return ok, error

    async def _run(self) -> None:
        while not self._stop:
            try:
                await asyncio.to_thread(self.sync_once)
            except asyncio.CancelledError:
                raise
            except Exception as e:   # pragma: no cover - the loop must never die
                self.stats["failures"] += 1
                self.stats["last_error"] = f"sync: {e}"
                print(f"[sheet] pipeline sync error: {e}", flush=True)
            await asyncio.sleep(max(60, settings.sheet_pipeline_sync_minutes * 60))

    def start(self) -> None:
        if not self.enabled:
            if settings.sheet_webhook_url and not self.users:
                print("[sheet] pipeline sync off (set SHEET_PIPELINE_USERS).", flush=True)
            return
        if self._task is None or self._task.done():
            self._stop = False
            self._task = asyncio.create_task(self._run())
            print(f"[sheet] pipeline sync started for {', '.join(self.users)} "
                  f"every {settings.sheet_pipeline_sync_minutes} min.", flush=True)

    async def stop(self) -> None:
        self._stop = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def status(self) -> dict:
        return {"enabled": self.enabled, "users": self.users,
                "every_minutes": settings.sheet_pipeline_sync_minutes, **self.stats}


pipeline_sync = PipelineSync()
