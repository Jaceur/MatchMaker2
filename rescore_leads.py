"""Re-score every already-enriched lead with the CURRENT scorer (scoring.py),
using the figures already stored on each lead — no internet calls.

Run this once after a scoring change so existing leads (and the admin slider's
"X leads clear this bar" preview) reflect the new logic, instead of the score
they happened to get the last time they were enriched.

    python rescore_leads.py

Safe + reversible: it only rewrites the derived `lead_score` column; the next
enrichment of a lead recomputes it anyway. Writes to the same Cloud SQL database
as the app, so the change shows up immediately.
"""
import logging
logging.getLogger("streamlit").setLevel(logging.ERROR)

from collections import Counter

from sqlalchemy import select, update, bindparam

from database import engine
from models import sales_leads
from scoring import score_lead, features_from_mapping


def main():
    print("Re-scoring all enriched leads from their stored figures...\n")

    # Every lead that has been through enrichment (i.e. isn't still 'sourced').
    with engine.connect() as conn:
        rows = conn.execute(
            select(sales_leads).where(sales_leads.c.status.is_distinct_from('sourced'))
        ).mappings().fetchall()

    if not rows:
        print("No enriched leads to re-score.")
        return

    updates = []
    changed = 0
    buckets = Counter()
    for row in rows:
        new = score_lead(features_from_mapping(row))
        if new != (row.get("lead_score") or 0):
            changed += 1
        updates.append({"lead_id_param": row["id"], "new_score": new})
        buckets[10 * (new // 10)] += 1

    # One efficient bulk write (executemany) rather than a query per lead.
    stmt = (update(sales_leads)
            .where(sales_leads.c.id == bindparam("lead_id_param"))
            .values(lead_score=bindparam("new_score")))
    with engine.begin() as conn:
        conn.execute(stmt, updates)

    total = len(updates)
    print(f"Re-scored {total} leads — {changed} scores changed.\n")
    print("New lead_score distribution:")
    for lo in range(0, 101, 10):
        c = buckets.get(lo, 0)
        bar = "#" * round(40 * c / total) if total else ""
        print(f"  {lo:3d}-{min(lo + 9, 100):<3d}: {bar} {c}")


if __name__ == "__main__":
    main()
