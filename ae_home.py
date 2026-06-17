"""AE Dashboard: a rep's personal overview — pipeline size, what they've saved to
Salesforce, their points, and a password-change form.

Distinct from ae_dashboard.py, which powers the "My Pipeline" working view.
"""
import streamlit as st
import pandas as pd
from sqlalchemy import text

from auth import change_password
from leaderboard import compute_points


@st.cache_data(ttl=60)
def _get_overview(_engine, username):
    """Pipeline count + raw activity counters for one AE."""
    with _engine.connect() as conn:
        pipeline = conn.execute(text("""
            SELECT COUNT(*) FROM sales_leads
            WHERE assigned_ae_username = :u AND status = 'approved'
        """), {"u": username}).scalar()
        stats = conn.execute(text("""
            SELECT COALESCE(urls_added, 0)   AS urls_added,
                   COALESCE(leads_swiped, 0) AS leads_swiped,
                   COALESCE(leads_saved, 0)  AS leads_saved
            FROM ae_stats WHERE username = :u
        """), {"u": username}).mappings().fetchone()
    stats = dict(stats) if stats else {"urls_added": 0, "leads_swiped": 0, "leads_saved": 0}
    return (pipeline or 0), stats


@st.cache_data(ttl=60)
def _get_saved_leads(_engine, username):
    """Leads this AE has saved into Salesforce — i.e. pressed Save (a CRM row)."""
    query = text("""
        SELECT sl.company_name      AS "Company",
               m.crm_status         AS "CRM Status",
               DATE(m.created_at)   AS "Saved"
        FROM ml_pipeline_analytics m
        JOIN sales_leads sl ON sl.id = m.lead_id
        WHERE m.swiped_by = :u AND m.crm_status IS NOT NULL
        ORDER BY m.created_at DESC
    """)
    with _engine.connect() as conn:
        return pd.read_sql(query, conn, params={"u": username})


def _clear_caches():
    _get_overview.clear()
    _get_saved_leads.clear()


def render_ae_dashboard(engine, username):
    st.title(f"🏠 {username.title()}'s Dashboard")

    pipeline_count, stats = _get_overview(engine, username)
    saved = _get_saved_leads(engine, username)
    points = compute_points(stats["urls_added"], stats["leads_swiped"], stats["leads_saved"])

    c1, c2, c3 = st.columns(3)
    c1.metric("Leads in Pipeline", pipeline_count)
    c2.metric("Into Salesforce", len(saved))
    c3.metric("Points", int(points))

    if st.button("Refresh"):
        _clear_caches()
        st.rerun()

    st.divider()

    # --- WHAT THEY'VE SAVED TO SALESFORCE ---
    st.subheader("📤 Leads you've saved to Salesforce")
    if saved.empty:
        st.info("Nothing saved yet — set a CRM status on a pipeline lead to add it.")
    else:
        st.dataframe(saved, hide_index=True, use_container_width=True)

    st.divider()

    # --- CHANGE PASSWORD ---
    st.subheader("🔑 Change Password")
    st.caption("Confirm the change by entering your current password.")
    with st.form("change_pw", clear_on_submit=True):
        current = st.text_input("Current password", type="password")
        new = st.text_input("New password", type="password")
        confirm = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Update password")
        if submitted:
            if new != confirm:
                st.error("New passwords don't match.")
            else:
                ok, msg = change_password(username, current, new)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
