"""One-time cleanup of broken lead assignments (safe to run once).

Two things drift over time and make the admin "Pending" numbers misleading:

  1. Leads the pipeline SCREENED OUT can still carry the AE name they had before
     — they shouldn't count as 'assigned' to anyone.
  2. A partial pipeline re-run can leave already-enriched leads stranded in status
     'sourced' (still named) instead of ready_for_swipe / screened_out.

This script fixes both: it re-derives the status of enriched 'sourced' leads from
their score (>= the current bar -> ready_for_swipe, else screened_out) and returns
them to the shared pool, then strips the AE name off every non-live lead. Genuine
pending / approved / archived assignments are left untouched.

    python fix_assignments.py

Writes to the same Cloud SQL database as the app. After running it, use the admin
dashboard's "Team Top-Up Allocation" to refill everyone's pending pile.
"""
import logging
logging.getLogger("streamlit").setLevel(logging.ERROR)

from sqlalchemy import text

from database import engine
from leads import release_dead_assignments
from settings import get_qualify_bar


def _snapshot(conn):
    return conn.execute(text("""
        SELECT
          COUNT(*) FILTER (WHERE status='sourced'      AND assigned_ae_username IS NOT NULL) AS sourced_named,
          COUNT(*) FILTER (WHERE status='screened_out' AND assigned_ae_username IS NOT NULL) AS screened_named,
          COUNT(*) FILTER (WHERE status='ready_for_swipe' AND assigned_ae_username IS NULL)  AS pool
        FROM sales_leads
    """)).mappings().fetchone()


def main():
    bar = get_qualify_bar()
    print(f"Qualification bar = {bar}\n")
    with engine.begin() as conn:
        before = _snapshot(conn)
        print(f"Before:  {before['sourced_named']} sourced-with-name, "
              f"{before['screened_named']} screened_out-with-name, pool={before['pool']}")

        # 1. Enriched leads stranded in 'sourced' (partial re-run leftovers): re-derive
        #    their status from their score and return them to the shared pool.
        recovered = conn.execute(text("""
            UPDATE sales_leads
            SET status = CASE WHEN lead_score >= :bar THEN 'ready_for_swipe' ELSE 'screened_out' END,
                assigned_ae_username = NULL, assigned_date = NULL
            WHERE status = 'sourced' AND lead_score IS NOT NULL
        """), {"bar": bar}).rowcount

        # 2. Strip the AE name off every non-live lead (screened_out etc.).
        released = release_dead_assignments(conn)

        after = _snapshot(conn)

    print(f"\nRecovered {recovered} enriched 'sourced' leads back into the pool.")
    print(f"Released  {released} AE names from non-live leads.")
    print(f"\nAfter:   {after['sourced_named']} sourced-with-name, "
          f"{after['screened_named']} screened_out-with-name, pool={after['pool']}")


if __name__ == "__main__":
    main()
