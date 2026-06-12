import streamlit as st
import pandas as pd
from sqlalchemy import text
import matchmaker2

def render_dashboard(engine):
    st.title("⚙️ Admin Control Center")
    st.write("Manage the Matchmaker 2.0 pipeline engine and monitor team output.")
    st.divider()

    # --- SECTION 1: THE PIPELINE CONTROLS ---
    st.markdown("### 🛠️ Data Pipeline Operations")
    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("📡 Run Sourcing API", use_container_width=True):
            with st.spinner("Querying Companies House..."):
                matchmaker2.run_sourcing_pipeline() 
            st.success("New leads sourced!")

    with col2:
        if st.button("🧠 Run Enrichment", use_container_width=True):
            with st.spinner("Scraping and scoring the web..."):
                matchmaker2.run_enrichment_pipeline()
            st.success("Leads enriched!")

    with col3:
        if st.button("🛑 Clear Database", type="primary", use_container_width=True):
            with st.spinner("Deleting records..."):
                matchmaker2.clear_all_data()
            st.warning("Database wiped.")

    st.divider()

    # --- SECTION 2: LIVE METRICS ---
    st.markdown("### 📊 Pipeline Health")
    
    with engine.connect() as conn:
        total_leads = conn.execute(text("SELECT COUNT(*) FROM sales_leads")).scalar()
        enriched_leads = conn.execute(text("SELECT COUNT(*) FROM sales_leads WHERE status != 'sourced'")).scalar()
        
    if total_leads > 0:
        enriched_pct = round((enriched_leads / total_leads) * 100, 1)
    else:
        enriched_pct = 0.0

    kpi1, kpi2, kpi3 = st.columns(3)
    kpi1.metric(label="Total Leads in System", value=total_leads)
    kpi2.metric(label="Enriched & Ready", value=enriched_leads)
    kpi3.metric(label="Enrichment Rate", value=f"{enriched_pct}%")

    st.markdown("<br>", unsafe_allow_html=True)

    # --- SECTION 3: TEAM PERFORMANCE & DATA TABLES ---
    col_team, col_data = st.columns(2)

    with col_team:
        st.markdown("### 🧑‍💻 AE Performance")
        user_stats_query = text("""
            SELECT 
                u.username AS "AE Name",
                COUNT(s.id) AS "Total Assigned",
                SUM(CASE WHEN s.status IN ('approved', 'archived') THEN 1 ELSE 0 END) AS "Processed",
                SUM(CASE WHEN s.status = 'ready_for_swipe' THEN 1 ELSE 0 END) AS "Pending"
            FROM users u
            LEFT JOIN sales_leads s ON u.username = s.assigned_ae
            WHERE u.role != 'admin'
            GROUP BY u.username
        """)
        user_stats_df = pd.read_sql(user_stats_query, engine)
        user_stats_df = user_stats_df.fillna(0).astype({'Total Assigned': 'int', 'Processed': 'int', 'Pending': 'int'})
        st.dataframe(user_stats_df, hide_index=True, use_container_width=True)

    with col_data:
        st.markdown("### 🏢 Latest Leads Preview")
        leads_preview_query = text("""
            SELECT company_name AS "Company", status AS "Status", assigned_ae AS "Assigned To" 
            FROM sales_leads 
            ORDER BY created_at DESC 
            LIMIT 100
        """)
        leads_preview_df = pd.read_sql(leads_preview_query, engine)
        st.dataframe(leads_preview_df, hide_index=True, use_container_width=True)