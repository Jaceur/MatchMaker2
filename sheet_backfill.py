"""Push already-archived high-value incorps into the Google Sheet.

The live feed only writes companies as they arrive, so a freshly-set-up sheet
starts empty. This replays what's already in `high_value_incorps` (the durable
archive) through the same row mapping the live feed uses, so the sheet opens with
history in it. The Apps Script de-duplicates on Company number, so re-running is
safe — a company already in the sheet is skipped, not doubled.

One-way, same as the live feed: this only POSTs to the Apps Script web app.

    python sheet_backfill.py                 # the last 7 days
    python sheet_backfill.py 30              # the last 30 days
    python sheet_backfill.py 2026-07-29      # one specific incorporation date

Needs SHEET_WEBHOOK_URL + SHEET_WEBHOOK_KEY in the root .env (the same values the
API service uses), plus the usual DB credentials.
"""
import json
import sys
from datetime import date, timedelta

import requests
from sqlalchemy import text

from api.config import settings
from api.routers.new_incorps import high_value_reasons
from api.sheet_sink import SHEET_COLUMNS, row_for
from database import engine

BATCH = 200          # rows per POST — matches the live feed's batch size


def fetch(since: date | None, on: date | None) -> list[dict]:
    """Archived high-value incorps, oldest first so the newest ends up on top of
    the sheet (each batch is inserted above the last)."""
    if on is not None:
        where, params = "h.date_of_creation = :d", {"d": on}
    else:
        where, params = "h.date_of_creation >= :d", {"d": since}
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT h.company_number, h.company_name, h.sic_codes, h.starting_capital,
                   h.corporate_owner, h.owner_name, h.city, h.date_of_creation,
                   h.received_at, h.lead
            FROM high_value_incorps h
            WHERE {where}
            ORDER BY h.date_of_creation ASC, h.received_at ASC
        """), params).mappings().fetchall()
    return [dict(r) for r in rows]


def to_event(row: dict) -> dict:
    """The original ingest event if we stored it (it has postcode + director
    details), falling back to the flat columns for any pre-`lead` rows."""
    event = dict(row.get("lead") or {})
    for key in ("company_number", "company_name", "starting_capital",
                "corporate_owner", "owner_name", "city"):
        event.setdefault(key, row.get(key))
    event.setdefault("sic_codes", row.get("sic_codes"))
    event.setdefault("date_of_creation", str(row.get("date_of_creation") or ""))
    event.setdefault("received_at", str(row.get("received_at") or ""))
    return event


def post(rows: list[dict]) -> bool:
    payload = {"key": settings.sheet_webhook_key, "columns": SHEET_COLUMNS,
               "sheets": {settings.sheet_tab_high_value: rows}}
    resp = requests.post(settings.sheet_webhook_url, data=json.dumps(payload, default=str),
                         headers={"Content-Type": "application/json"}, timeout=120)
    body = (resp.text or "")[:300]
    ok = resp.ok and '"ok":true' in body.replace(" ", "")
    if not ok:
        print(f"  FAILED: HTTP {resp.status_code} {body!r}")
    return ok


def main() -> None:
    if not settings.sheet_webhook_url or not settings.sheet_webhook_key:
        raise SystemExit("Set SHEET_WEBHOOK_URL and SHEET_WEBHOOK_KEY in .env first "
                         "(see google_sheet/README.md).")

    arg = sys.argv[1] if len(sys.argv) > 1 else "7"
    if "-" in arg:
        on, since = date.fromisoformat(arg), None
        print(f"Backfilling high-value incorps incorporated on {on}...")
    else:
        on, since = None, date.today() - timedelta(days=int(arg))
        print(f"Backfilling high-value incorps incorporated since {since}...")

    archived = fetch(since, on)
    print(f"{len(archived)} archived rows found.")
    sent = 0
    for start in range(0, len(archived), BATCH):
        chunk = archived[start:start + BATCH]
        rows = []
        for r in chunk:
            event = to_event(r)
            # Every archived row qualified when it arrived, but the oldest ones
            # predate the stored `lead` blob, so the reasons can't be recomputed
            # — say so rather than leaving the column blank and implying "no".
            reasons = high_value_reasons(event) or ["Archived high value"]
            rows.append(row_for(event, reasons))
        if post(rows):
            sent += len(rows)
            print(f"  sent {sent}/{len(archived)}")
    print(f"Done — {sent} rows pushed. Duplicates were skipped by the Apps Script.")


if __name__ == "__main__":
    main()
