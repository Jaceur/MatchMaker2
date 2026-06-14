"""Lead data operations: queries, feature engineering, and admin mutations.

These are the read/write helpers that sit on top of the sales_leads table and
are shared by the UI pages and the CLI.
"""
from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy import select, delete, text

from database import engine
from models import sales_leads


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


# ==========================================
# DATA LOADING
# ==========================================
@st.cache_data(ttl=600)
def get_pending_leads(ae_username):
    """Fetches this AE's unprocessed leads, best score first.
    Cached so the database isn't queried on every single click."""
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
    print("Connecting to database to initiate wipe...")
    with engine.begin() as connection:
        result = connection.execute(delete(sales_leads))
        print(f"SUCCESS: Wiped {result.rowcount} records from the sales_leads table.")
        return result.rowcount


def clear_all_data():
    print("UI Triggered: Wiping database...")
    wiped = clear_database()
    return f"Database wiped. {wiped} records deleted."
