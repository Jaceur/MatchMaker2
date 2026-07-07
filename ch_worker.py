"""Always-on worker for the CH Lead Engine — one process for a single ~$5/mo
service (Railway / Fly.io / a small VM).

It runs the whole real-time pipeline in one process so you don't need to keep
two things alive:

  - main thread:        the /companies stream listener — new incorporations are
                        written to ch_queue the moment they're filed.
  - background thread:  every ENRICH_INTERVAL_SECONDS, drain the queue (enrich +
                        score each company) and sweep for SH01/MR01 trigger
                        events over REST.

Config comes from ENVIRONMENT VARIABLES (no secrets.toml needed on the host) —
see database.py / ch_client.py. Set these on the platform:

    DB_PASSWORD, SUPABASE_HOST, SUPABASE_USER      (SUPABASE_PORT/DBNAME optional)
    CH_API_KEY        (REST — enrichment, event sweep)
    CH_STREAM_KEY     (streaming — the /companies listener)
    ENRICH_INTERVAL_SECONDS  (optional, default 900 = 15 min)
    ENRICH_BATCH             (optional, default 200 companies per cycle)

The REST rate limiter in ch_client is process-wide and thread-safe, so the
enrichment thread and any stream-side REST calls share the 550-per-5-min budget
safely. Run it with:  python ch_worker.py
"""
import os
import threading
import time

from ch_enrich import drain_queue, sweep_events, pending_count
from ch_stream import run_companies_listener

ENRICH_INTERVAL = int(os.environ.get("ENRICH_INTERVAL_SECONDS", "900"))
ENRICH_BATCH = int(os.environ.get("ENRICH_BATCH", "200"))


def _enrichment_loop():
    """Drain + score the queue and sweep events, forever, on a timer. Runs in a
    daemon thread; any error in one cycle is logged and the loop continues."""
    while True:
        try:
            queued = pending_count()
            if queued:
                print(f"[worker] enriching (queue={queued})...", flush=True)
                counts = drain_queue(limit=ENRICH_BATCH)
                applied = sweep_events(limit=ENRICH_BATCH)
                print(f"[worker] scored {counts['scored']}, recheck "
                      f"{counts['recheck']}, failed {counts['failed']}, "
                      f"events {applied}", flush=True)
            else:
                print("[worker] queue empty, nothing to enrich", flush=True)
        except Exception as e:  # never let a bad cycle kill the loop
            print(f"[worker] enrichment cycle error: {e}", flush=True)
        time.sleep(ENRICH_INTERVAL)


def main():
    print(f"[worker] starting — enrich every {ENRICH_INTERVAL}s, "
          f"batch {ENRICH_BATCH}", flush=True)
    threading.Thread(target=_enrichment_loop, daemon=True).start()
    # Blocks forever, reconnecting on drops (jittered backoff) and resuming from
    # the last saved timepoint so no incorporations are missed across restarts.
    run_companies_listener()


if __name__ == "__main__":
    main()
