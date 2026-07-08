"""Railway lead worker: source + enrich the MAIN Streamlit pipeline on demand.

This is a separate always-on service from the Companies House -> Google Sheet
stream worker (that now lives in its own repo, github.com/Jaceur/CHStream). It
sources + enriches the MAIN Streamlit lead pipeline. It watches pipeline_jobs;
when the
admin queues a job from the Streamlit dashboard ("source N leads + enrich"),
it:

  1. sources ~N net-new leads — one fresh RANDOM incorporation date per batch
     of up to 100 (the lightest way to keep the random-date behaviour, since
     the CH advanced-search endpoint returns at most 100 per call),
  2. runs the full staged screening pipeline (pipeline.run_pipeline) over every
     'sourced' lead — including any left over from before, so a failed or
     cancelled job simply continues where it stopped when you queue the next one,
  3. writes progress back to the job row every few leads, so the dashboard can
     show live progress bars, and stops mid-job if the admin hits Cancel.

Config — ENVIRONMENT VARIABLES (no secrets.toml on Railway):
    DB_PASSWORD, SUPABASE_HOST, SUPABASE_USER    (SUPABASE_PORT/DBNAME optional)
    CH_API_KEY                                    (REST key — sourcing + enrichment)

Run:  python lead_worker.py     (set this as the service's start command)
"""
import logging
import time
from datetime import datetime

# Quieten Streamlit's "missing ScriptRunContext" warnings when run headless.
logging.getLogger("streamlit").setLevel(logging.ERROR)

from sqlalchemy import select, update, func, text  # noqa: E402

from database import engine                        # noqa: E402
from models import pipeline_jobs, sales_leads      # noqa: E402
from sourcing import source_leads                  # noqa: E402
from pipeline import run_pipeline                  # noqa: E402

POLL_SECONDS = 10          # how often to look for a new pending job
PROGRESS_EVERY = 5         # write enrichment progress to the DB every N leads
CANCEL_CHECK_EVERY = 20    # re-read the job status every N leads


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
                time.sleep(POLL_SECONDS)
        except Exception as e:
            # DB blip etc. — log and keep polling; never let the worker die.
            print(f"[leads] poll error: {e}", flush=True)
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
