"""Railway lead worker: source + enrich leads on demand AND on a schedule.

Always-on service (separate from the CHStream new-incorps worker, github.com/
Jaceur/CHStream). It watches pipeline_jobs; when a job appears — queued by an
admin from the React panel, OR by this worker's own auto-scheduler (below) — it:

  1. sources ~N net-new leads — one fresh RANDOM incorporation date per batch
     of up to 100 (the lightest way to keep the random-date behaviour, since
     the CH advanced-search endpoint returns at most 100 per call),
  2. runs the full staged screening pipeline (pipeline.run_pipeline) over every
     'sourced' lead — including any left over from before, so a failed or
     cancelled job simply continues where it stopped when you queue the next one,
  3. writes progress back to the job row every few leads, so the panel can
     show live progress bars, and stops mid-job if the admin hits Cancel.

AUTO-SCHEDULER (maybe_auto_source): when idle, roughly every 4 hours it enqueues
a 500-lead source+enrich job — but only while the unassigned-and-qualified buffer
is under 1000, so it stops topping up once there's plenty waiting and resumes
when the buffer drains. All four knobs are live-tunable via app_settings; see
maybe_auto_source. Auto-jobs are normal pipeline_jobs rows (requested_by=
'auto-scheduler'), so they show up in the admin panel exactly like manual ones.

Config — ENVIRONMENT VARIABLES (no .env file on Railway):
    DB_PASSWORD, SUPABASE_HOST, SUPABASE_USER    (SUPABASE_PORT/DBNAME optional)
    CH_API_KEY                                    (REST key — sourcing + enrichment)

Run:  python lead_worker.py     (set this as the service's start command)
"""
import time
from datetime import datetime

from sqlalchemy import insert, select, update, func, text

from database import engine
from models import pipeline_jobs, sales_leads
from sourcing import source_leads
from pipeline import run_pipeline
from settings import get_int_setting, get_setting, set_setting, get_qualify_bar

POLL_SECONDS = 10          # how often to look for a new pending job
PROGRESS_EVERY = 5         # write enrichment progress to the DB every N leads
CANCEL_CHECK_EVERY = 20    # re-read the job status every N leads
AUTO_CHECK_SECONDS = 300   # how often to run the auto-source check (not per poll)


class JobCancelled(Exception):
    """Raised inside a progress callback to abort a running job cleanly."""


def _claim_next_job():
    """Atomically flip the oldest pending job to running and return it."""
    with engine.begin() as conn:
        return conn.execute(text("""
            UPDATE pipeline_jobs
               SET status = 'running', started_at = now(), updated_at = now()
             WHERE id = (SELECT id FROM pipeline_jobs
                          WHERE status = 'pending' ORDER BY id LIMIT 1)
         RETURNING id, requested
        """)).first()


def _update_job(job_id, **values):
    values["updated_at"] = datetime.utcnow()
    with engine.begin() as conn:
        conn.execute(update(pipeline_jobs)
                     .where(pipeline_jobs.c.id == job_id).values(**values))


def _is_cancelled(job_id):
    with engine.connect() as conn:
        status = conn.execute(
            select(pipeline_jobs.c.status).where(pipeline_jobs.c.id == job_id)
        ).scalar()
    return status == "cancelled"


def run_job(job_id, requested, resume=False):
    # --- 1. Source (random date per ~100-lead batch) -----------------------
    # On resume (a job orphaned by a crashed worker) sourcing is skipped — the
    # leads it already sourced are still 'sourced' in the pool; we just finish
    # enriching them below.
    if resume:
        with engine.connect() as conn:
            sourced = conn.execute(
                select(pipeline_jobs.c.sourced)
                .where(pipeline_jobs.c.id == job_id)
            ).scalar() or 0
        print(f"[leads] job {job_id}: RESUMING after a crash — skipping "
              f"sourcing ({sourced} already sourced), finishing enrichment",
              flush=True)
    else:
        print(f"[leads] job {job_id}: source {requested} + enrich — starting",
              flush=True)

        def on_sourced(done, total):
            _update_job(job_id, sourced=done)
            print(f"[leads] job {job_id}: sourced {done}/{total}", flush=True)

        sourced = source_leads(requested, progress_callback=on_sourced,
                               should_stop=lambda: _is_cancelled(job_id))
        if _is_cancelled(job_id):
            _update_job(job_id, sourced=sourced, finished_at=datetime.utcnow(),
                        message=f"Cancelled during sourcing ({sourced} leads kept).")
            print(f"[leads] job {job_id}: cancelled during sourcing", flush=True)
            return

    # --- 2. Enrich every 'sourced' lead (incl. leftovers from past runs) ---
    with engine.connect() as conn:
        to_enrich = conn.execute(
            select(func.count()).select_from(sales_leads)
            .where(sales_leads.c.status == "sourced")
        ).scalar() or 0
    _update_job(job_id, sourced=sourced, to_enrich=to_enrich)

    state = {"done": 0}

    def on_enriched(done, total, company_name):
        state["done"] = done
        if done % PROGRESS_EVERY == 0 or done == total:
            _update_job(job_id, enriched=done)
        if done % CANCEL_CHECK_EVERY == 0 and _is_cancelled(job_id):
            raise JobCancelled()
        if done % 25 == 0:
            print(f"[leads] job {job_id}: enriched {done}/{total}", flush=True)

    try:
        enriched = run_pipeline(limit=None, progress_callback=on_enriched)
        _update_job(job_id, status="done", enriched=enriched,
                    finished_at=datetime.utcnow(),
                    message=f"Sourced {sourced} new leads; enriched {enriched}.")
        print(f"[leads] job {job_id}: done — sourced {sourced}, "
              f"enriched {enriched}", flush=True)
    except JobCancelled:
        _update_job(job_id, enriched=state["done"], finished_at=datetime.utcnow(),
                    message=f"Cancelled after enriching {state['done']} of "
                            f"{to_enrich}. Queue a new job to continue.")
        print(f"[leads] job {job_id}: cancelled during enrichment", flush=True)
    except Exception as e:
        # Everything enriched so far is already saved (run_pipeline commits per
        # lead); the rest stay 'sourced', so the next job picks them up.
        _update_job(job_id, status="failed", enriched=state["done"],
                    finished_at=datetime.utcnow(),
                    message=f"Failed after {state['done']} of {to_enrich}: "
                            f"{str(e)[:300]}")
        print(f"[leads] job {job_id}: FAILED — {e}", flush=True)


def _resume_orphaned_jobs():
    """Any job still 'running' at startup was orphaned — this single worker is
    the only thing that sets 'running', so if we're just starting, a running job
    means a previous instance crashed mid-job (e.g. an OOM). Resume each: its
    enriched leads are saved and its remaining 'sourced' leads just need
    finishing, so it never gets stuck on the dashboard."""
    with engine.connect() as conn:
        orphaned = conn.execute(
            select(pipeline_jobs.c.id, pipeline_jobs.c.requested)
            .where(pipeline_jobs.c.status == "running")
            .order_by(pipeline_jobs.c.id)
        ).fetchall()
    for row in orphaned:
        print(f"[leads] orphaned job {row.id} found at startup — resuming",
              flush=True)
        try:
            run_job(row.id, row.requested or 0, resume=True)
        except Exception as e:
            print(f"[leads] resume of job {row.id} errored: {e}", flush=True)


# ---------------------------------------------------------------------------
# Auto-scheduler
# ---------------------------------------------------------------------------
_last_auto_check = 0.0     # in-memory throttle so the check runs ~every 5 min,
                          # not on every 10s poll (the real cadence gate is the
                          # DB-persisted last_auto_source_at, below).


def _auto_source_decision(*, enabled, job_in_flight, elapsed_hours,
                          interval_hours, awaiting, cap):
    """Should we enqueue an auto source+enrich job right now? Pure — no DB, so
    it's unit-testable. Returns (do_source, reason).

    `elapsed_hours=None` means we've never auto-sourced → treat as due. Note we
    only advance the persisted clock when we ACTUALLY source (see caller), so
    being over `cap` just holds off without resetting the timer — the moment the
    buffer dips back under the cap (and the interval has passed) it sources,
    which is the 'off until it drops below 1000 again' behaviour."""
    if not enabled:
        return False, "auto-source disabled"
    if job_in_flight:
        return False, "a job is already pending/running"
    if elapsed_hours is not None and elapsed_hours < interval_hours:
        return False, f"only {elapsed_hours:.1f}h since last (< {interval_hours}h)"
    if awaiting >= cap:
        return False, f"buffer {awaiting} >= cap {cap} — holding off"
    return True, f"buffer {awaiting} < cap {cap} and {interval_hours}h elapsed"


def maybe_auto_source():
    """Enqueue a scheduled source+enrich job when it's due and the buffer is low.

    Live-tunable via app_settings (insert a row to override the default; delete
    it to revert):
      auto_source_enabled         (default 1)     0 to switch the scheduler off
      auto_source_interval_hours  (default 4)
      auto_source_batch           (default 500)   leads per auto-job
      auto_source_max_awaiting    (default 1000)  pause above this many waiting
    """
    global _last_auto_check
    now = time.monotonic()
    if now - _last_auto_check < AUTO_CHECK_SECONDS:
        return
    _last_auto_check = now

    interval_hours = get_int_setting("auto_source_interval_hours", 4)
    batch = get_int_setting("auto_source_batch", 500)
    cap = get_int_setting("auto_source_max_awaiting", 1000)
    enabled = bool(get_int_setting("auto_source_enabled", 1))

    # Hours since the last job we actually enqueued (persisted, so a worker
    # restart / redeploy doesn't reset the cadence). Unset/garbled → due.
    last = get_setting("last_auto_source_at")
    elapsed_hours = None
    if last:
        try:
            elapsed_hours = (datetime.utcnow() - datetime.fromisoformat(last)).total_seconds() / 3600
        except ValueError:
            pass

    bar = get_qualify_bar()
    with engine.connect() as conn:
        job_in_flight = conn.execute(text(
            "SELECT 1 FROM pipeline_jobs WHERE status IN ('pending', 'running') LIMIT 1"
        )).first() is not None
        # "Awaiting assignment" = enriched, qualified (or holdout), not yet handed
        # to an AE — the same pool leads.top_up_allocation draws from.
        awaiting = conn.execute(text("""
            SELECT count(*) FROM sales_leads
            WHERE status = 'ready_for_swipe'
              AND assigned_ae_username IS NULL
              AND (lead_score >= :bar OR is_holdout IS TRUE)
        """), {"bar": bar}).scalar() or 0

    do_source, reason = _auto_source_decision(
        enabled=enabled, job_in_flight=job_in_flight, elapsed_hours=elapsed_hours,
        interval_hours=interval_hours, awaiting=awaiting, cap=cap,
    )
    if not do_source:
        # Only chatter when it's a state worth seeing (over cap), not every tick.
        if enabled and not job_in_flight and awaiting >= cap:
            print(f"[auto] {reason}", flush=True)
        return

    with engine.begin() as conn:
        conn.execute(insert(pipeline_jobs).values(
            job_type="source_enrich", requested=batch, status="pending",
            requested_by="auto-scheduler",
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ))
    set_setting("last_auto_source_at", datetime.utcnow().isoformat())
    print(f"[auto] queued source+enrich of {batch} — {reason}", flush=True)


def main():
    print(f"[leads] worker started — polling for jobs every {POLL_SECONDS}s",
          flush=True)
    _resume_orphaned_jobs()          # finish anything a crash left mid-flight
    while True:
        try:
            job = _claim_next_job()
            if job:
                run_job(job.id, job.requested or 0)
            else:
                # Idle: see if a scheduled auto-source is due, then wait. An
                # enqueued auto-job is claimed on the next poll, same as a manual.
                maybe_auto_source()
                time.sleep(POLL_SECONDS)
        except Exception as e:
            # DB blip etc. — log and keep polling; never let the worker die.
            print(f"[leads] poll error: {e}", flush=True)
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
