"""One-time cleanup: retire the pre-pipeline "legacy" leads from the live pool.

WHAT / WHY
----------
230 decided leads (134 approved, 96 passed) predate the staged pipeline — they
have NO screening_log row. They were decided in June under an older scoring
regime, and later rescores pushed their displayed lead_score into the 0-39
bands, where (combined with Clear-working-pool deleting passes but keeping
approvals) they made low scores look BETTER than mid scores on the analytics
board (0-19: 45% vs 40-59: 37% — while the honest durable log has 0-19 at 0%).

Order matters — RESCUE BEFORE REMOVE:
  1. Snapshot every legacy lead's features into screening_log (provenance-marked).
     129 of them carry real approve/pass labels in ml_pipeline_analytics but were
     invisible to the trainer for want of a screening_log row to join on — this
     step both preserves their features and ADDS them to the training set.
  2. Archive the approved ones into pipeline_archive (same pattern as
     leads.clear_pipeline) and remove them from the live pool.
  3. Delete the passed ones (their features are now durably held by step 1;
     their labels were always durable in ml_pipeline_analytics).

Idempotent: every step is guarded, so re-running is a no-op. All three steps run
in ONE transaction — it either all happens or none of it does.

    python cleanup_legacy_leads.py            # shows the plan, then executes
"""
from sqlalchemy import text

from database import engine

# A "legacy" lead: decided (approved/archived) but never screened by the staged
# pipeline — i.e. no screening_log row. Membership by provenance, NOT by score:
# legacy leads exist in every band and should all go.
LEGACY_FILTER = """
    sl.status IN ('approved', 'archived')
    AND NOT EXISTS (SELECT 1 FROM screening_log s WHERE s.lead_id = sl.id)
"""

RESCUE_SQL = text(f"""
    INSERT INTO screening_log
        (lead_id, crn, company_name, sic_codes, incorporation_date,
         account_type, employee_count, turnover, cash_at_bank, foreign_exchange,
         trade_debtors, trade_creditors, import_activity, export_activity,
         director_change_recent, confidence_score, website_score, linkedin_score,
         lead_score, sic_multiplier, qualified, is_holdout, screen_reason, created_at)
    SELECT sl.id, sl.crn, sl.company_name, sl.sic_codes, sl.incorporation_date,
           sl.account_type, sl.employee_count, sl.turnover, sl.cash_at_bank,
           sl.foreign_exchange, sl.trade_debtors, sl.trade_creditors,
           sl.import_activity, sl.export_activity, sl.director_change_recent,
           sl.confidence_score, sl.website_score, sl.linkedin_score,
           sl.lead_score, sl.sic_multiplier,
           TRUE,        -- qualified: they did reach AEs, under the old regime
           FALSE,       -- is_holdout: the holdout didn't exist yet
           'LEGACY · pre-pipeline lead, features backfilled at cleanup 2026-07-18',
           now()
    FROM sales_leads sl
    WHERE {LEGACY_FILTER}
""")

ARCHIVE_SQL = text(f"""
    INSERT INTO pipeline_archive ({{cols}})
    SELECT {{cols}} FROM sales_leads sl
    WHERE sl.status = 'approved'
      AND NOT EXISTS (SELECT 1 FROM pipeline_archive pa WHERE pa.id = sl.id)
      AND EXISTS (SELECT 1 FROM screening_log s WHERE s.lead_id = sl.id
                  AND s.screen_reason LIKE 'LEGACY%')
""")

DELETE_SQL = text("""
    DELETE FROM sales_leads sl
    USING screening_log s
    WHERE s.lead_id = sl.id
      AND s.screen_reason LIKE 'LEGACY%'
      AND sl.status IN ('approved', 'archived')
""")


def main():
    with engine.connect() as conn:
        n = conn.execute(text(
            f"SELECT count(*), count(*) FILTER (WHERE sl.status='approved') "
            f"FROM sales_leads sl WHERE {LEGACY_FILTER}"
        )).fetchone()
    print(f"Legacy leads in the live pool: {n[0]}  ({n[1]} approved, {n[0]-n[1]} passed)")
    if n[0] == 0:
        print("Nothing to do — already clean.")
        return

    # pipeline_archive mirrors sales_leads column-for-column (models.py keeps
    # them in step); copy by explicit shared column list, like clear_pipeline.
    with engine.connect() as conn:
        share = [r[0] for r in conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'sales_leads'
            INTERSECT
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'pipeline_archive'
        """)).fetchall()]
    cols = ", ".join(share)

    with engine.begin() as conn:
        rescued = conn.execute(RESCUE_SQL).rowcount
        archived = conn.execute(text(str(ARCHIVE_SQL.text).format(cols=cols))).rowcount
        deleted = conn.execute(DELETE_SQL).rowcount

    print(f"1. RESCUE : {rescued} feature snapshots inserted into screening_log")
    print(f"2. ARCHIVE: {archived} approved legacy leads copied to pipeline_archive")
    print(f"3. REMOVE : {deleted} legacy leads deleted from the live pool")
    print("\nDone. The analytics board's low bands now reflect only pipeline-era leads.")


if __name__ == "__main__":
    main()
