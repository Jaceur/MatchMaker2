import streamlit as st
import pandas as pd
from sqlalchemy import text

# Cached at module level so reruns (every button click, every widget interaction)
# don't hammer the DB. The leading underscore on _engine tells Streamlit not to
# try hashing the engine object — only ttl + username invalidate the cache.
@st.cache_data(ttl=300)
def get_approved_leads(_engine, username: str) -> pd.DataFrame:
    query = text("""
        SELECT
            company_name      AS "Company",
            confidence_score  AS "Match %",
            website_url       AS "Website",
            linkedin_url      AS "LinkedIn",
            is_nabd           AS "NAB'd",
            DATE(updated_at)  AS "Date Approved"
        FROM sales_leads
        WHERE assigned_ae_username = :username
          AND status = 'approved'
        ORDER BY updated_at DESC
    """)
    with _engine.connect() as conn:
        return pd.read_sql(query, conn, params={"username": username})


def render_ae_pipeline(engine, current_username: str):
    st.title("🚀 My Approved Pipeline")
    st.write("Leads you have validated and are ready for outreach.")
    st.divider()

    df = get_approved_leads(engine, current_username)

    if df.empty:
        st.info("You haven't approved any leads yet. Get swiping!")
        if st.button("Refresh"):
            get_approved_leads.clear()
            st.rerun()
        return

    # Lightweight summary row — cheap because it reuses the cached frame,
    # no extra queries.
    col1, col2, col3 = st.columns(3)
    col1.metric("Approved Leads", len(df))
    col2.metric("Avg Match Score", f"{df['Match %'].mean():.0f}%")
    col3.metric("With Website", int(df["Website"].notna().sum()))

    st.dataframe(
        df,
        column_config={
            "Match %": st.column_config.ProgressColumn(
                "Match %", min_value=0, max_value=100, format="%d%%"
            ),
            "Website": st.column_config.LinkColumn("Website", display_text="Visit"),
            "LinkedIn": st.column_config.LinkColumn("LinkedIn", display_text="Profile"),
            "NAB'd": st.column_config.CheckboxColumn("NAB'd", disabled=True),
        },
        hide_index=True,
        use_container_width=True,
    )

    if st.button("Refresh My Pipeline"):
        get_approved_leads.clear()
        st.rerun()