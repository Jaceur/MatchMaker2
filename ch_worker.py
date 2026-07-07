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
    CH_API_KEY        (REST — enrichment, event sweep)   <-- REQUIRED for enrichment
    CH_STREAM_KEY     (streaming — the /companies listener)
    ENRICH_BATCH             (optional, default 100 companies per drain)
    ENRICH_IDLE_SECONDS      (optional, default 20 — poll gap when queue empty)
    SWEEP_INTERVAL_SECONDS   (optional, default 900 — event-sweep cadence)

The REST rate limiter in ch_client is process-wide and thread-safe, so the
enrichment thread and any stream-side REST calls share the 550-per-5-min budget
safely. Run it with:  python ch_worker.py
"""
import os
import threading
import time

from ch_enrich import drain_queue, sweep_events, pending_count
from ch_stream import run_companies_listener

ENRICH_BATCH = int(os.environ.get("ENRICH_BATCH", "100"))
# How long to wait before polling the queue again ONCE IT'S EMPTY. Small, so a
# newly-streamed incorporation is enriched within seconds (this is a race —
# first to contact wins). When there IS work, we drain back-to-back with no wait.
IDLE_SECONDS = int(os.environ.get("ENRICH_IDLE_SECONDS", "20"))
# The SH01/MR01 event sweep is expensive (2 REST calls per tracked company), so
# it runs on its own slower cadence, only while the enrichment queue is idle.
SWEEP_SECONDS = int(os.environ.get("SWEEP_INTERVAL_SECONDS", "900"))


def _enrichment_loop():
    """Enrich the queue as fast as new companies arrive: drain back-to-back
    while there's work, idle only when empty, and run the event sweep on a
    slower cadence. Daemon thread — any error is logged and the loop continues."""
    last_sweep = 0.0
    while True:
        try:
            if pending_count():
                counts = drain_queue(limit=ENRICH_BATCH)
                print(f"[worker] scored {counts['scored']}, recheck "
                      f"{counts['recheck']}, failed {counts['failed']}", flush=True)
                # Straight back to draining if more arrived meanwhile; a short
                # gap keeps this from becoming a hot loop if every item fails.
                time.sleep(2)
                continue

            now = time.time()
            if now - last_sweep >= SWEEP_SECONDS:
                applied = sweep_events(limit=ENRICH_BATCH)
                last_sweep = now
                if applied:
                    print(f"[worker] {applied} trigger event(s) applied", flush=True)
        except Exception as e:  # never let a bad cycle kill the loop
            print(f"[worker] enrichment cycle error: {e}", flush=True)
        time.sleep(IDLE_SECONDS)


def main():
    print(f"[worker] starting — enrich batch {ENRICH_BATCH}, idle poll "
          f"{IDLE_SECONDS}s, event sweep every {SWEEP_SECONDS}s", flush=True)
    threading.Thread(target=_enrichment_loop, daemon=True).start()
    # Blocks forever, reconnecting on drops (jittered backoff) and resuming from
    # the last saved timepoint so no incorporations are missed across restarts.
    run_companies_listener()


if __name__ == "__main__":
    main()
