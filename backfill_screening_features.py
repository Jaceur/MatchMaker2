"""One-time backfill: fill the recently-added screening_log feature columns for
historical rows, from sales_leads (live leads) and pipeline_archive (cleared /
approved leads). Those columns (sic_codes, incorporation_date, and the
web-presence scores) were added after most leads were already screened, so they're
null in the training log — which means the model is training blind to them.

Safe + idempotent: only fills nulls (COALESCE keeps any existing value), and can
be re-run. Run locally against the DB.

    python backfill_screening_features.py
"""
from sqlalchemy import text

from database import engine

# Columns to backfill, and the source tables that still hold the values.
COLS = ["sic_codes", "incorporation_date", "confidence_score", "website_score", "linkedin_score"]


def _backfill(source: str) -> int:
    sets = ", ".join(f"{c} = COALESCE(s.{c}, src.{c})" for c in COLS)
    need_null = " OR ".join(f"s.{c} IS NULL" for c in COLS)
    sql = text(
        f"UPDATE screening_log s SET {sets} "
        f"FROM {source} src WHERE src.id = s.lead_id AND ({need_null})"
    )
    with engine.begin() as conn:
        return conn.execute(sql).rowcount


def main():
    n1 = _backfill("sales_leads")
    n2 = _backfill("pipeline_archive")
    with engine.connect() as conn:
        cov = conn.execute(text(
            "SELECT COUNT(*) AS total, COUNT(sic_codes) AS sic, "
            "COUNT(confidence_score) AS conf, COUNT(incorporation_date) AS inc "
            "FROM screening_log"
        )).mappings().fetchone()
    print(f"Backfilled: {n1} rows from sales_leads, {n2} from pipeline_archive.")
    print(f"screening_log coverage now — SIC {cov['sic']}/{cov['total']}, "
          f"confidence {cov['conf']}/{cov['total']}, inc-date {cov['inc']}/{cov['total']}")


if __name__ == "__main__":
    main()
