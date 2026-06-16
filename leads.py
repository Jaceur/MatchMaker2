"""Lead data operations: queries, feature engineering, and admin mutations.

These are the read/write helpers that sit on top of the sales_leads table and
are shared by the UI pages and the CLI.
"""
from datetime import datetime

import pandas as pd
from sqlalchemy import select, delete, insert, text

from database import engine
from models import sales_leads, pipeline_archive


# ==========================================
# FEATURE ENGINEERING
# ==========================================
def engineer_ml_features(current_lead):
    """Converts raw database strings into numerical features for Machine
    Learning. Shared by the swipe page (Pass) and My Pipeline (Approve)."""
    try:
        incorp_date = pd.to_datetime(current_lead['incorporation_date'])
        age_in_days = (pd.Timestamp.now() - incorp_date).days
        company_age_months = max(0, age_in_days // 30)
    except Exception:
        company_age_months = 0

    directors = current_lead.get('active_directors', '')
    if not directors or pd.isna(directors):
        director_count = 0
    else:
        director_count = len(str(directors).split(','))

    return company_age_months, director_count


def build_ml_row(lead, swiped_by, **overrides):
    """Build the fields the Pass and Approve paths share when logging to
    ml_pipeline_analytics, then layer on per-decision values via `overrides`
    (is_worth_it, crm_status, rejection_reason, dwell_time_seconds, the
    website/linkedin verdicts, corrected URLs). Keeps the three score columns
    distinct instead of triple-storing one number."""
    age_months, dir_count = engineer_ml_features(lead)
    row = {
        "lead_id": lead["id"],
        "crn": lead["crn"],
        "company_age_months": age_months,
        "director_count": dir_count,
        "website_score": lead.get("website_score") or 0,
        "linkedin_score": lead.get("linkedin_score") or 0,
        "overall_score": lead.get("confidence_score") or 0,
        "swiped_by": swiped_by,
    }
    row.update(overrides)
    return row


# ==========================================
# DATA LOADING
# ==========================================
def get_pending_leads(ae_username):
    """Fetches this AE's unprocessed leads, best score first. Not cached: the
    swipe page holds the result in a session queue (the real cache) and only
    calls this when that queue is empty."""
    with engine.connect() as conn:
        query = (
            select(sales_leads)
            .where(
                (sales_leads.c.status == 'ready_for_swipe')
                & (sales_leads.c.assigned_ae_username == ae_username)
            )
            .order_by(sales_leads.c.confidence_score.desc())
        )
        return [dict(row) for row in conn.execute(query).mappings().fetchall()]


# ==========================================
# ADMIN MUTATIONS
# ==========================================
def assign_leads_to_ae(username, num_leads):
    print(f"Assigning {num_leads} leads to {username}...")
    with engine.begin() as connection:
        # We use a subquery to grab the best unassigned leads
        assign_query = text("""
            UPDATE sales_leads
            SET assigned_ae_username = :username,
                assigned_date = :now
            WHERE id IN (
                SELECT id FROM sales_leads
                WHERE status = 'ready_for_swipe' AND assigned_ae_username IS NULL
                ORDER BY confidence_score DESC
                LIMIT :limit
            )
        """)

        result = connection.execute(assign_query, {
            "username": username,
            "now": datetime.utcnow(),
            "limit": num_leads
        })

        return result.rowcount


def clear_database():
    """Wipe the working pool — sourced / ready_for_swipe / passed leads — but
    PRESERVE approved pipeline leads. Returns how many working leads were removed.
    """
    print("Clearing working pool (approved pipeline preserved)...")
    with engine.begin() as connection:
        # is_distinct_from keeps NULL-status rows in the 'delete' set too.
        result = connection.execute(
            delete(sales_leads).where(sales_leads.c.status.is_distinct_from('approved'))
        )
        print(f"SUCCESS: Removed {result.rowcount} working leads; approved pipeline kept.")
        return result.rowcount


def clear_all_data():
    print("UI Triggered: Clearing working pool...")
    wiped = clear_database()
    return f"Cleared {wiped} working leads. Approved pipeline preserved."


def clear_pipeline():
    """Snapshot approved pipeline leads into pipeline_archive, then remove them
    from the live table. Returns how many were archived + cleared."""
    print("Archiving + clearing approved pipeline...")
    snapshot_cols = [c.name for c in sales_leads.c]
    with engine.begin() as connection:
        # 1. Copy approved leads into the permanent archive.
        connection.execute(
            insert(pipeline_archive).from_select(
                snapshot_cols,
                select(sales_leads).where(sales_leads.c.status == 'approved'),
            )
        )
        # 2. Remove them from the live pipeline.
        result = connection.execute(
            delete(sales_leads).where(sales_leads.c.status == 'approved')
        )
        print(f"SUCCESS: Archived + cleared {result.rowcount} pipeline leads.")
        return result.rowcount


def clear_pipeline_data():
    print("UI Triggered: Clearing pipeline...")
    moved = clear_pipeline()
    return f"Archived + cleared {moved} pipeline leads."
