import streamlit as st
import pandas as pd
from sqlalchemy import text

def render_ae_pipeline(engine, current_username):
    st.title("🚀 My Approved Pipeline")
    st.write("Leads you have validated and are ready for outreach.")
    st.divider()

    # Query the database for this specific user's approved leads
    query = text("""
        SELECT 
            company_name AS "Company",
            confidence_score AS "Match %",
            website_url AS "Website",
            linkedin_url AS "LinkedIn",
            is_nabd AS "NAB'd",
            DATE(updated_at) AS "Date Approved"
        FROM sales_leads
        WHERE assigned_ae_username = :username AND status = 'approved'
        ORDER BY updated_at DESC
    """)

    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"username": current_username})

    if df.empty:
        st.info("You haven't approved any leads yet. Get swiping!")
    else:
        # Display the dataframe with clickable links
        st.dataframe(
            df,
            column_config={
                "Website": st.column_config.LinkColumn("Website"),
                "LinkedIn": st.column_config.LinkColumn("LinkedIn")
            },
            hide_index=True,
            use_container_width=True
        )