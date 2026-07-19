"""One-time: drop the dead is_nabd / contact_email columns.

These were removed from the ORM (models.py) long ago but left physically in the
DB because the retired Streamlit app still SELECTed them. Streamlit is gone
(2026-07-17) and nothing in the React/API/worker stack references them — a repo
grep finds zero uses. So the physical columns are pure dead weight; this removes
them from both tables that carry them.

DESTRUCTIVE and irreversible (the data in these columns is discarded). It's also
the point — is_nabd was the retired "Won" flag, contact_email an abandoned field.
Idempotent: DROP COLUMN IF EXISTS, so re-running is a no-op.

    python drop_dead_columns.py
"""
from sqlalchemy import text

from database import engine

DEAD_COLUMNS = ["is_nabd", "contact_email"]
TABLES = ["sales_leads", "pipeline_archive"]


def main():
    with engine.connect() as conn:
        present = {
            t: [r[0] for r in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = ANY(:cols)"
            ), {"t": t, "cols": DEAD_COLUMNS}).fetchall()]
            for t in TABLES
        }
    total = sum(len(v) for v in present.values())
    print("Dead columns found:")
    for t, cols in present.items():
        print(f"   {t}: {cols or '(none)'}")
    if total == 0:
        print("Nothing to drop — already clean.")
        return

    with engine.begin() as conn:
        for t in TABLES:
            for col in DEAD_COLUMNS:
                conn.execute(text(f"ALTER TABLE {t} DROP COLUMN IF EXISTS {col}"))
    print(f"\nDropped {total} column(s). Done.")


if __name__ == "__main__":
    main()
