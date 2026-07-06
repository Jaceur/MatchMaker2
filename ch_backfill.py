"""Backfill for the CH Lead Engine: ingest recently incorporated companies via
the REST advanced-search endpoint — no stream key needed.

This is both the Phase-1 bootstrap (fill the queue with the last few days
before the stream listeners take over) and a perfectly good daily alternative
to running the /companies stream at all: run it once a day from cron/Task
Scheduler and you get the same companies, just up to a day later.

Usage:
    python ch_backfill.py           # ingest the last 3 days
    python ch_backfill.py 7         # ingest the last 7 days
    python ch_backfill.py 7 500     # ...capped at 500 companies per day

Idempotent: companies already seen are skipped (upsert on company_number), so
overlapping runs and stream/backfill overlap are harmless.
"""
import sys
import time
from datetime import datetime, timedelta

import ch_client
from ch_enrich import queue_company
from database import engine

PAGE_SIZE = 100          # advanced-search max page size
DEFAULT_DAYS = 3
DEFAULT_DAILY_CAP = 5000  # safety valve; ~2,000-3,500 UK incorporations/day


def backfill_day(date_str, daily_cap=DEFAULT_DAILY_CAP):
    """Ingest every active ltd/plc/llp incorporated on one date. Returns
    (seen, new)."""
    seen = new = 0
    start_index = 0
    while seen < daily_cap:
        results = ch_client.advanced_search({
            "incorporated_from": date_str,
            "incorporated_to": date_str,
            "company_status": "active",
            "company_type": "ltd,plc,llp",
            "size": PAGE_SIZE,
            "start_index": start_index,
        })
        items = (results or {}).get("items", []) or []
        if not items:
            break

        with engine.begin() as conn:
            for item in items:
                number = item.get("company_number")
                if not number:
                    continue
                seen += 1
                if queue_company(
                    conn, number,
                    name=item.get("company_name"),
                    date_of_creation=item.get("date_of_creation") or date_str,
                ):
                    new += 1

        if len(items) < PAGE_SIZE:
            break
        start_index += PAGE_SIZE
    return seen, new


def backfill(days=DEFAULT_DAYS, daily_cap=DEFAULT_DAILY_CAP):
    """Ingest the last `days` days of incorporations. Returns total new."""
    total_new = 0
    today = datetime.utcnow().date()
    # Oldest first, so if a run is interrupted the freshest day (most likely
    # to still be missing PSC data anyway) is the one left over.
    for offset in range(days, 0, -1):
        date_str = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        start = time.time()
        seen, new = backfill_day(date_str, daily_cap)
        total_new += new
        print(f"{date_str}: {seen} companies seen, {new} new "
              f"({time.time() - start:.0f}s)")
    return total_new


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DAYS
    cap = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DAILY_CAP
    print(f"Backfilling the last {days} day(s) of incorporations "
          f"(cap {cap}/day)...\n")
    total = backfill(days, cap)
    print(f"\nDone — {total} new companies queued. "
          f"Run 'python ch_run_local.py' to enrich and score them.")
