"""One-way push of new incorporations into a Google Sheet.

    CHStream --POST--> API /new-incorps/ingest --> [this module] --POST--> Apps Script --> Sheet

**The data flow is one-way by construction.** This module only ever makes
outbound POSTs to the Apps Script web app; there is no read path, no Sheets API
client and no Google credentials anywhere in the backend. Nothing an AE types in
the sheet can travel back into Matchmaker, so the sheet is safe to hand out — the
worst a bad actor with the sheet can do is edit their own copy of the data.

Two latency tiers, because they answer different questions:

* **High-value** rows are the first-to-contact race, so they go **in real time**:
  `enqueue` wakes the flusher immediately and the POST starts at once.
* **All** rows are a log, so they ride a 15s batch. Batching that half is what
  keeps ~2,000 incorporations/day from becoming 2,000 Apps Script calls — its
  daily runtime quota (90 min personal / 6 h Workspace) is the real constraint
  here, not our CPU. Set `SHEET_FLUSH_SECONDS=0` to make everything real-time,
  but watch the quota if you do.

Everything is fail-safe: `enqueue` is synchronous, non-blocking and swallows its
own errors, so a broken sheet (or a wrong URL, or Google being down) can never
slow down or break ingest — the live stream and the DB archive carry on.
"""
import asyncio
import json
import time
from collections import deque
from datetime import datetime, timezone

import requests

from .config import settings

# ---- Tunables -------------------------------------------------------------
# The two wait times come from config (env SHEET_FLUSH_SECONDS_HIGH_VALUE /
# SHEET_FLUSH_SECONDS) so pacing can be retuned without a deploy. Read once at
# import — a restart applies a new value.
HV_FLUSH_SECONDS = settings.sheet_flush_seconds_high_value   # the race
ALL_FLUSH_SECONDS = settings.sheet_flush_seconds             # the log
MAX_BATCH = 200             # rows per POST (Apps Script handles this comfortably)
MAX_PENDING = 5_000         # backlog cap; oldest dropped past this (never grow unbounded)
IDLE_WAIT_SECONDS = 30      # how long the flusher sleeps with nothing queued
                            # (only a ceiling — an arriving row wakes it instantly)
SEND_TIMEOUT = 60           # Apps Script can be slow to wake; be patient
SEND_RETRIES = 3
DEDUPE_MEMORY = 10_000      # recent (tab, company_number) keys held to skip repeats

# The default column order, used ONLY when the Apps Script has to create a tab
# from scratch. After that the sheet's own header row is the source of truth —
# the script maps each row object by header NAME, so you can reorder or delete
# columns in the sheet and nothing here needs to change.
SHEET_COLUMNS = [
    "Received", "Company", "Company number", "Incorporated", "SIC codes",
    "City", "Postcode", "Starting capital", "First director", "PSC",
    "Corporate owner", "Owner name",
    "First name", "Last name", "Director DOB", "Director residence",
    "Other directorships", "High value", "Why high value",
    "Companies House", "LinkedIn",
]

_CH_URL = "https://find-and-update.company-information.service.gov.uk/company/"
_LI_SEARCH = "https://www.linkedin.com/search/results/people/?keywords="


def _sic_text(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if v)
    return str(value or "")


def _capital_cell(value):
    """A number the sheet can sum/sort, or "" — never the string "0" for absent
    (CHStream sends "" when a company has no capital statement at all, which is
    NOT the same as £0 of capital)."""
    if value is None or value == "":
        return ""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return ""


def _linkedin_search(first: str, last: str) -> str:
    name = " ".join(p for p in (first, last) if p).strip()
    if not name:
        return ""
    return _LI_SEARCH + requests.utils.quote(name)


def row_for(event: dict, reasons: list[str] | None = None) -> dict:
    """Map an ingest event to a sheet row (column name -> cell value).

    Keys the sheet doesn't have a column for are dropped by the Apps Script, so
    adding one here is safe even before the sheet gains the column.
    """
    reasons = reasons or []
    first = str(event.get("director_first_name") or "")
    last = str(event.get("director_last_name") or "")
    crn = str(event.get("company_number") or "")
    return {
        "Received": (event.get("received_at")
                     or datetime.now(timezone.utc).isoformat())[:19].replace("T", " "),
        "Company": str(event.get("company_name") or ""),
        "Company number": crn,
        "Incorporated": str(event.get("date_of_creation") or "")[:10],
        "SIC codes": _sic_text(event.get("sic_codes")),
        "City": str(event.get("city") or ""),
        "Postcode": str(event.get("postcode") or ""),
        "Starting capital": _capital_cell(event.get("starting_capital")),
        # Who RUNS it vs who OWNS it — two different conversations. The fallback
        # covers events sent by a CHStream that predates these fields: the merged
        # name is PSC-preferred, so it's the best guess available for a director.
        "First director": str(event.get("first_director_name")
                              or " ".join(p for p in (first, last) if p)),
        "PSC": str(event.get("psc_names") or ""),
        "Corporate owner": "Yes" if event.get("corporate_owner") else "",
        "Owner name": str(event.get("owner_name") or ""),
        "First name": first,
        "Last name": last,
        "Director DOB": str(event.get("director_dob") or ""),
        "Director residence": str(event.get("director_residence") or ""),
        "Other directorships": event.get("director_other_companies", ""),
        "High value": "Yes" if reasons else "",
        "Why high value": "; ".join(reasons),
        "Companies House": (_CH_URL + crn) if crn else "",
        "LinkedIn": _linkedin_search(first, last),
    }


def should_flush(pending: int, oldest_age: float, has_high_value: bool) -> bool:
    """Pure flush decision — the whole scheduling policy in one testable place.

    Flush when the batch is full, or when the oldest row has waited long enough:
    `HV_FLUSH_SECONDS` if any high-value row is waiting (0 = real time — the
    race), `ALL_FLUSH_SECONDS` otherwise (15s — the log).
    """
    if pending <= 0:
        return False
    if pending >= MAX_BATCH:
        return True
    return oldest_age >= (HV_FLUSH_SECONDS if has_high_value else ALL_FLUSH_SECONDS)


def post_payload(payload: dict, label: str = "payload") -> tuple[bool, str | None]:
    """POST one prepared payload to the Apps Script web app, with backoff.

    The single outbound door — the live feed and the pipeline sync both go
    through it, so auth, retries and "what counts as success" are defined once.
    BLOCKING (uses `requests`): call it via `asyncio.to_thread`, never inline on
    the event loop.
    """
    if not settings.sheet_webhook_key:
        # A published Apps Script URL is public; never post to one unauthenticated.
        return False, "SHEET_WEBHOOK_KEY not set - refusing to post to a public web app."
    body_out = json.dumps({**payload, "key": settings.sheet_webhook_key}, default=str)
    last = None
    for attempt in range(SEND_RETRIES):
        try:
            # Apps Script /exec answers with a 302 to script.googleusercontent —
            # requests follows it by default, which is what we want.
            resp = requests.post(
                settings.sheet_webhook_url,
                data=body_out,
                headers={"Content-Type": "application/json"},
                timeout=SEND_TIMEOUT,
            )
            if resp.ok:
                body = (resp.text or "")[:200]
                # Apps Script returns 200 even for an application-level error (a
                # bad key, an exception in the script), so read the body.
                if '"ok":true' in body.replace(" ", ""):
                    return True, None
                last = f"Apps Script rejected it: {body!r}"
                break                # a rejection won't fix itself on retry
            last = f"HTTP {resp.status_code}: {(resp.text or '')[:200]!r}"
        except requests.RequestException as e:
            last = str(e)
        if attempt < SEND_RETRIES - 1:
            time.sleep(2 ** attempt)
    print(f"[sheet] {label} failed: {last}", flush=True)
    return False, last


class SheetSink:
    """Buffers rows and POSTs them to the Apps Script web app in batches."""

    def __init__(self) -> None:
        self._pending: deque = deque()          # (queued_at, tab, row, is_hv)
        self._seen_keys: set = set()
        self._seen_order: deque = deque()
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()            # a new row interrupts the wait
        self._stop = False
        self.stats = {"queued": 0, "sent": 0, "dropped": 0, "failed_batches": 0,
                      "last_sent_at": None, "last_error": None}

    # ---- configuration ----------------------------------------------------
    @property
    def enabled(self) -> bool:
        """BOTH halves are required. Half-configured used to count as enabled,
        which meant the sink happily queued rows it could never send and dropped
        them a minute later — 362 leads lost before anyone looked at the stats.
        Missing either value now means OFF, and `start()` says which one."""
        return bool(settings.sheet_webhook_url and settings.sheet_webhook_key)

    # ---- producer side (called from the ingest request — must never block) --
    def _dedupe(self, key: str) -> bool:
        """True if this (tab, company number) is new. CH re-sends a company on
        every update, and CHStream's own seen-set resets when it restarts."""
        if key in self._seen_keys:
            return False
        self._seen_keys.add(key)
        self._seen_order.append(key)
        if len(self._seen_order) > DEDUPE_MEMORY:
            self._seen_keys.discard(self._seen_order.popleft())
        return True

    def enqueue(self, event: dict, reasons: list[str]) -> None:
        """Queue one incorporation for the sheet. Cheap, synchronous, and total:
        any failure is swallowed so ingest is never affected."""
        if not self.enabled:
            return
        try:
            row = row_for(event, reasons)
            targets = []
            if settings.sheet_send_all:
                targets.append((settings.sheet_tab_all, False))
            if reasons:
                targets.append((settings.sheet_tab_high_value, True))
            now = _now()
            for tab, is_hv in targets:
                if not self._dedupe(f"{tab}|{row['Company number']}"):
                    continue
                self._pending.append((now, tab, row, is_hv))
                self.stats["queued"] += 1
            while len(self._pending) > MAX_PENDING:
                self._pending.popleft()
                self.stats["dropped"] += 1
            # Wake the flusher NOW rather than letting it find this on its next
            # poll. With a 0-second wait (high value) that's the difference
            # between "real time" and "within a second".
            self._wake.set()
        except Exception as e:      # pragma: no cover - belt and braces
            self.stats["last_error"] = f"enqueue: {e}"

    # ---- consumer side (background task) ----------------------------------
    def _deadline(self) -> float:
        """Seconds the oldest pending row may still wait. 0 = send it now."""
        if not self._pending:
            return IDLE_WAIT_SECONDS
        has_hv = any(item[3] for item in self._pending)
        wait = HV_FLUSH_SECONDS if has_hv else ALL_FLUSH_SECONDS
        return max(0.0, wait - (_now() - self._pending[0][0]))

    def _due(self) -> bool:
        if not self._pending:
            return False
        oldest_at = self._pending[0][0]
        has_hv = any(item[3] for item in self._pending)
        return should_flush(len(self._pending), _now() - oldest_at, has_hv)

    def _take_batch(self) -> dict[str, list[dict]]:
        """Pop up to MAX_BATCH rows, grouped by tab (one POST covers both tabs)."""
        batch: dict[str, list[dict]] = {}
        for _ in range(min(MAX_BATCH, len(self._pending))):
            _, tab, row, _hv = self._pending.popleft()
            batch.setdefault(tab, []).append(row)
        return batch

    async def _flush(self) -> None:
        batch = self._take_batch()
        count = sum(len(rows) for rows in batch.values())
        # requests is blocking — off the event loop, or a slow Apps Script call
        # would stall every SSE client on this process.
        ok, error = await asyncio.to_thread(self._post, batch)
        if ok:
            self.stats["sent"] += count
            self.stats["last_sent_at"] = datetime.now(timezone.utc).isoformat()
            self.stats["last_error"] = None
        else:
            # Dropped, not requeued: these are time-critical leads, and a
            # retry-forever queue would replay a stale backlog over the live feed
            # once the sheet came back. The DB archive is the durable copy.
            self.stats["failed_batches"] += 1
            self.stats["dropped"] += count
            self.stats["last_error"] = error

    def _post(self, batch: dict[str, list[dict]]) -> tuple[bool, str | None]:
        """POST one batch of new rows. Runs in a worker thread."""
        return post_payload({"columns": SHEET_COLUMNS, "sheets": batch},
                            label=f"batch of {sum(len(r) for r in batch.values())}")

    async def _run(self) -> None:
        """Sleep until the oldest pending row is due — or until `enqueue` wakes
        us, whichever comes first. Only ONE POST is ever in flight (the flush is
        awaited here), so a burst naturally coalesces into a bigger batch instead
        of stampeding Apps Script, whose script lock would serialise it anyway."""
        while not self._stop:
            try:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self._deadline())
                except asyncio.TimeoutError:
                    pass             # the deadline came first — that's fine
                self._wake.clear()
                if self._due():
                    await self._flush()
            except asyncio.CancelledError:
                raise
            except Exception as e:   # pragma: no cover - the loop must never die
                self.stats["last_error"] = f"flusher: {e}"
                print(f"[sheet] flusher error: {e}", flush=True)

    # ---- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if not self.enabled:
            # Name the missing half. "Sink is off" on its own reads like a
            # deliberate config and sends you looking in the wrong place.
            missing = "SHEET_WEBHOOK_URL" if not settings.sheet_webhook_url else "SHEET_WEBHOOK_KEY"
            print(f"[sheet] {missing} is NOT SET - Google Sheet feed is OFF "
                  f"(both SHEET_WEBHOOK_URL and SHEET_WEBHOOK_KEY are required).", flush=True)
            return
        if self._task is None or self._task.done():
            self._stop = False
            self._task = asyncio.create_task(self._run())
            mode = "all + high-value" if settings.sheet_send_all else "high-value only"
            pace = (f"high-value {'REAL TIME' if HV_FLUSH_SECONDS <= 0 else f'{HV_FLUSH_SECONDS}s'}, "
                    f"rest {'REAL TIME' if ALL_FLUSH_SECONDS <= 0 else f'{ALL_FLUSH_SECONDS}s'}")
            print(f"[sheet] sink started ({mode}; {pace}) -> "
                  f"{settings.sheet_webhook_url[:60]}...", flush=True)

    async def stop(self) -> None:
        """Flush what's left, then stop — a redeploy shouldn't lose the buffer."""
        self._stop = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._pending and self.enabled:
            try:
                await self._flush()
            except Exception as e:   # pragma: no cover
                print(f"[sheet] final flush failed: {e}", flush=True)

    def status(self) -> dict:
        return {"enabled": self.enabled,
                "send_all": settings.sheet_send_all,
                "pending": len(self._pending),
                **self.stats}


def _now() -> float:
    """Monotonic clock for the age-based flush rule — immune to NTP steps, and
    usable from tests with no event loop running."""
    return time.monotonic()


sink = SheetSink()
