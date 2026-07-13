"""Re-run the staged pipeline over the still-in-play leads only — those
'ready_for_swipe' (awaiting allocation, or assigned but not yet swiped) — so each
is re-enriched and re-scored from scratch with the CURRENT scoring. Screened-out
leads and already-swiped leads (approved / archived) are left completely untouched.

    python rerun_pipeline.py

It resets those leads to 'sourced' and runs the full pipeline (Companies House ->
accounts -> website) on every sourced lead. SLOW — it re-fetches the internet
for each lead, one at a time — so run it locally (Stage B's accounts parsing/OCR
is local-only). It writes to the same Cloud SQL database as the app, and saves
each lead as it goes, so it's safe to stop and re-run (it picks up where it left
off). AE assignments on pending leads are preserved.
"""
import logging
logging.getLogger("streamlit").setLevel(logging.ERROR)

import time

from sqlalchemy import update, select, func

from database import engine
from models import sales_leads
from pipeline import run_pipeline  # noqa: E402  (after logging setup)

# Leads to send back through the pipeline: only the still-in-play ones —
# 'ready_for_swipe' (awaiting allocation OR assigned but not yet swiped). We
# deliberately DON'T re-run 'screened_out' leads (they were binned and don't
# need re-enriching) or swiped leads ('approved' / 'archived', left untouched).
RESET_STATUSES = ('ready_for_swipe',)


def _progress(done, total, name):
    bar_len = 30
    filled = int(bar_len * done / total) if total else bar_len
    bar = "█" * filled + "─" * (bar_len - filled)
    print(f"\r[{bar}] {done}/{total}  {(name or '')[:38]:<38}", end="", flush=True)


def main():
    with engine.connect() as conn:
        to_reset = conn.execute(
            select(func.count()).select_from(sales_leads)
            .where(sales_leads.c.status.in_(RESET_STATUSES))
        ).scalar() or 0
        already_sourced = conn.execute(
            select(func.count()).select_from(sales_leads)
            .where(sales_leads.c.status == 'sourced')
        ).scalar() or 0

    total = to_reset + already_sourced
    print(f"Re-running the pipeline on {total} leads:")
    print(f"  - {to_reset} ready-to-swipe (awaiting / assigned, not swiped)  (reset to 'sourced')")
    print(f"  - {already_sourced} already sourced")
    print("  Screened-out and swiped (approved / archived) leads are left untouched.\n")
    if total == 0:
        print("Nothing to do.")
        return
    if input("This re-fetches Companies House + accounts + website for each (slow). "
             "Continue? (y/n): ").strip().lower() != 'y':
        print("Cancelled.")
        return

    # 1. Put the non-swiped leads back into the pipeline's queue.
    with engine.begin() as conn:
        conn.execute(
            update(sales_leads)
            .where(sales_leads.c.status.in_(RESET_STATUSES))
            .values(status='sourced', screen_reason=None)
        )
    print(f"Reset {to_reset} leads to 'sourced'.\n")

    # 2. Run the full staged pipeline over every sourced lead.
    start = time.time()
    count = run_pipeline(progress_callback=_progress)
    print(f"\n\nDone — re-ran {count} leads in {(time.time() - start) / 60:.1f} min.")


if __name__ == "__main__":
    main()
