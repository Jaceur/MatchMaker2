"""One-time backfill: write the missing ML label rows for approved leads.

WHY
---
Until 2026-07-18, the ML label row was only written at CLASSIFY — so an approve
the AE never classified never became training data. That silently leaked 271
approvals (170 still-live, 101 archived at the legacy cleanup) while the whole
training set held only 275. Approves now label at swipe time (api/services.py);
this script recovers the ones that fell through before the fix.

WHAT IT WRITES
--------------
One ml_pipeline_analytics row per trapped approval: is_worth_it=TRUE (the
swipe-level fact — these leads WERE approved), features from wherever the lead
now lives (sales_leads or pipeline_archive), crm_status left NULL (still
classifiable for live leads; unknowable for archived ones), created_at set to
the lead's updated_at so time-ordered readers see roughly the approve date.
hours_in_queue is left NULL — computing it now would measure "until today",
not "until the decision".

Idempotent: only writes for leads with NO labelled row; re-running is a no-op.
Everything runs in one transaction.

RUN AFTER the 2026-07-18 API deploy (the pipeline-page queries must already key
off crm_status, not row-existence, or the new rows hide live leads from the
classify list):

    python backfill_approval_labels.py
"""
from sqlalchemy import text

from database import engine

# Shared shape: pull the features build_ml_row would have used, from either home.
# swiped_by = the assigned AE (the approver on every path this app has had).
_INSERT = """
    INSERT INTO ml_pipeline_analytics
        (lead_id, crn, company_age_months, director_count,
         website_score, linkedin_score, overall_score,
         website_valid, linkedin_valid,
         corrected_website_url, corrected_linkedin_url,
         website_candidates, linkedin_candidates, website_chosen, linkedin_chosen,
         lead_score, sic_multiplier, is_holdout,
         is_worth_it, dwell_time_seconds, swiped_by, created_at)
    SELECT src.id, src.crn,
           GREATEST(0, (EXTRACT(EPOCH FROM (now() - src.incorporation_date::timestamp)) / 2592000))::int,
           CASE WHEN COALESCE(src.active_directors, '') = '' THEN 0
                ELSE array_length(string_to_array(src.active_directors, ','), 1) END,
           COALESCE(src.website_score, 0), COALESCE(src.linkedin_score, 0),
           COALESCE(src.confidence_score, 0),
           src.website_accurate, src.linkedin_accurate,
           src.corrected_website_url, src.corrected_linkedin_url,
           src.website_candidates, src.linkedin_candidates,
           COALESCE(src.corrected_website_url, src.website_url),
           COALESCE(src.corrected_linkedin_url, src.linkedin_url),
           src.lead_score, src.sic_multiplier, src.is_holdout,
           TRUE, src.approve_dwell_seconds, src.assigned_ae_username,
           COALESCE(src.updated_at, now())
    FROM {table} src
    WHERE src.status = 'approved'
      AND NOT EXISTS (SELECT 1 FROM ml_pipeline_analytics m
                      WHERE m.lead_id = src.id AND m.is_worth_it IS NOT NULL)
"""


def main():
    with engine.connect() as conn:
        live, archived = (
            conn.execute(text(f"""
                SELECT count(*) FROM {t} src
                WHERE src.status = 'approved'
                  AND NOT EXISTS (SELECT 1 FROM ml_pipeline_analytics m
                                  WHERE m.lead_id = src.id AND m.is_worth_it IS NOT NULL)
            """)).scalar()
            for t in ("sales_leads", "pipeline_archive")
        )
    print(f"Trapped approvals — live: {live}, archived: {archived}")
    if not live and not archived:
        print("Nothing to do — no approvals are missing labels.")
        return

    # Archive first, then live: a lead somehow present in both (shouldn't happen,
    # but the guard makes order irrelevant anyway) is only written once.
    with engine.begin() as conn:
        n_arch = conn.execute(text(_INSERT.format(table="pipeline_archive"))).rowcount
        n_live = conn.execute(text(_INSERT.format(table="sales_leads"))).rowcount
    print(f"Backfilled labels: {n_live} from the live pipeline, {n_arch} from the archive.")

    with engine.connect() as conn:
        total = conn.execute(text(
            "SELECT count(DISTINCT lead_id) FROM ml_pipeline_analytics "
            "WHERE is_worth_it IS TRUE"
        )).scalar()
    print(f"Approvals now carrying a label: {total}.")
    print("Re-run train_model.py to see the effect.")


if __name__ == "__main__":
    main()
