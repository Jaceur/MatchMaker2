"""The labelled-lead dataset: ONE query joining features to verdicts.

Every consumer of training data — the model trainer (train_model.py), the SIC
weighting (sic_weights.py), and whatever comes next — must read labels through
here, for two reasons:

1. THE JOIN IS SUBTLE. Features come from the latest screening_log row per lead
   (the durable screen-time snapshot); the label is BOOL_OR(is_worth_it) over
   ml_pipeline_analytics (a lead re-classified later counts as approved if it
   ever was). Duplicating that logic invites drift between consumers.

2. THE SOURCE MATTERS. This is the DURABLE log, deliberately NOT the live
   sales_leads pool: `leads.clear_database()` deletes passes and keeps
   approvals, so the live pool's approval rate inflates with every "Clear"
   (measured: 51% there vs 34% here). Training or weighting off the live pool
   would bake that bias in. Never "optimise" this back to sales_leads.
"""
import pandas as pd
from sqlalchemy import text

from database import engine

LABELLED_LEADS_SQL = text("""
    WITH latest_features AS (
        SELECT DISTINCT ON (lead_id) *
        FROM screening_log
        WHERE lead_id IS NOT NULL
        ORDER BY lead_id, created_at DESC
    ),
    verdicts AS (
        SELECT lead_id, BOOL_OR(is_worth_it) AS approved
        FROM ml_pipeline_analytics
        WHERE lead_id IS NOT NULL AND is_worth_it IS NOT NULL
        GROUP BY lead_id
    )
    SELECT f.*, v.approved
    FROM latest_features f
    JOIN verdicts v ON v.lead_id = f.lead_id
""")


def load_labelled_leads() -> pd.DataFrame:
    """One row per decided lead: every screening_log feature + `approved`."""
    with engine.connect() as conn:
        return pd.read_sql(LABELLED_LEADS_SQL, conn)
