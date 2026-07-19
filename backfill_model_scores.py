"""One-time backfill: populate model_score on existing rows so the admin
shadow-model scoreboard has data immediately, instead of waiting weeks for new
leads to flow through enrichment and get swiped.

Scores are computed by model_scorer (the exact serve path), from features the
rows already hold:
  - screening_log  — the DURABLE source the shadow AUC/precision reads (decided
    leads live here). This is the one that makes the scoreboard light up.
  - sales_leads    — the live pool, for the coverage % metric.

Only fills NULLs (idempotent — safe to re-run, e.g. after retraining, though a
retrain that should overwrite everything is better done by clearing the column
first). Needs scikit-learn + joblib (run in the ML venv or any env with them).

    python backfill_model_scores.py
"""
from sqlalchemy import bindparam, select, text, update

from database import engine
from models import screening_log, sales_leads
from model_scorer import score_lead_model, model_available

# The columns model_scorer needs; both tables carry all of them.
_COLS = [
    "website_score", "linkedin_score", "confidence_score",
    "sic_codes", "incorporation_date", "account_type",
    "employee_count", "turnover", "cash_at_bank", "foreign_exchange",
    "trade_debtors", "trade_creditors",
    "import_activity", "export_activity", "director_change_recent",
]


def _backfill(table, id_col="id"):
    """Score every row of `table` whose model_score is NULL. Returns (seen, set)."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(table).where(table.c.model_score.is_(None))
        ).mappings().fetchall()

    updates = []
    for r in rows:
        ms = score_lead_model(r)
        if ms is not None:
            updates.append({"_id": r[id_col], "_ms": ms})

    if updates:
        stmt = (update(table)
                .where(table.c[id_col] == bindparam("_id"))
                .values(model_score=bindparam("_ms")))
        with engine.begin() as conn:
            conn.execute(stmt, updates)
    return len(rows), len(updates)


def main():
    if not model_available():
        print("No model file (lead_model.pkl) — nothing to backfill. Train + commit first.")
        return

    for name, table, idc in (("screening_log", screening_log, "id"),
                             ("sales_leads", sales_leads, "id")):
        seen, done = _backfill(table, idc)
        skipped = seen - done
        print(f"{name}: {done} scored"
              f"{f', {skipped} skipped (never reached Stage C — no web features)' if skipped else ''}")

    with engine.connect() as conn:
        decided = conn.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT DISTINCT ON (s.lead_id) s.lead_id
                FROM screening_log s
                JOIN ml_pipeline_analytics m
                  ON m.lead_id = s.lead_id AND m.is_worth_it IS NOT NULL
                WHERE s.model_score IS NOT NULL
                ORDER BY s.lead_id, s.created_at DESC
            ) t
        """)).scalar()
    print(f"\nDecided leads now carrying a model score (the shadow scoreboard's n): {decided}")


if __name__ == "__main__":
    main()
