"""Drain the CH Lead Engine queue locally: enrich + score every queued
company. The cron entrypoint — schedule it every ~15 minutes (Task Scheduler /
cron) and it naturally spreads the REST calls across the day, or run it by
hand after a backfill. Mirrors enrich_local.py: writes straight to Cloud SQL,
so results appear on the High Quality New Incorps page immediately.

Usage:
    python ch_run_local.py          # process up to 200 queued companies
    python ch_run_local.py 50       # process up to 50
    python ch_run_local.py all      # drain the whole queue

Needs .streamlit/secrets.toml (CH_API_KEY + DB credentials), same as the app.
"""
import logging
import sys
import time

# Quieten Streamlit's "missing ScriptRunContext" warnings when run outside the app.
logging.getLogger("streamlit").setLevel(logging.ERROR)

from ch_enrich import drain_queue, pending_count, sweep_events  # noqa: E402


def _progress(done, total, company_number):
    bar_len = 30
    filled = int(bar_len * done / total) if total else bar_len
    bar = "█" * filled + "─" * (bar_len - filled)
    print(f"\r[{bar}] {done}/{total}  {company_number:<12}", end="", flush=True)


def main():
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "200"
    if arg == "all":
        limit = None
    elif arg.isdigit():
        limit = int(arg)
    else:
        print(f"Usage: python ch_run_local.py [N | all]   (got {arg!r})")
        return

    queued = pending_count()
    target = "ALL" if limit is None else f"up to {limit}"
    print(f"{queued} companies queued; processing {target} (writing to Supabase)...\n")

    start = time.time()
    counts = drain_queue(limit=limit, progress_callback=_progress)
    elapsed = (time.time() - start) / 60
    print(f"\n\nEnriched in {elapsed:.1f} min — scored {counts['scored']}, "
          f"awaiting PSC recheck {counts['recheck']}, failed {counts['failed']}.")

    # Then sweep tracked young companies for SH01/MR01 trigger events over REST
    # (this is what replaces the /filings stream — the ONLY stream we run is
    # /companies for new incorporations). 'all' also unbounds the sweep.
    print("\nChecking for trigger events (SH01/MR01) via REST...")
    applied = sweep_events(limit=None if limit is None else max(limit, 300),
                           progress_callback=_progress)
    print(f"\n\nEvent sweep done — {applied} new trigger event(s) applied "
          "(promoted to Tier 1).")


if __name__ == "__main__":
    main()
