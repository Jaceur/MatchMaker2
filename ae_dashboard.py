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
                result_msg = matchmaker2.run_sourcing_pipeline()
            st.success(result_msg)  # shows the real count, e.g. "87 new leads added"

    with col2:
        if st.button("🧠 Run Enrichment", use_container_width=True):
            with st.spinner("Scraping and scoring the web..."):
                result_msg = matchmaker2.run_enrichment_pipeline()
            st.success(result_msg)

    with col3:
        # A two-step wipe: the button does nothing unless the box is
        # ticked. One stray click can no longer delete the whole pipeline.
        confirm_wipe = st.checkbox("I understand this deletes ALL leads")
        if st.button("🛑 Clear Database", type="primary", use_container_width=True):
            if confirm_wipe:
                with st.spinner("Deleting records..."):
                    result_msg = matchmaker2.clear_all_data()
                st.warning(result_msg)
            else:
                st.error("Tick the confirmation box first.")

    st.divider()

    # --- SECTION 1.5: MANUAL LEAD ALLOCATION ---
    st.markdown("### 🎯 Manual Lead Allocation")

    with engine.connect() as conn:
        # Fetch a list of all non-admin users to populate the dropdown
        users_df = pd.read_sql(text("SELECT username FROM users WHERE role != 'admin'"), conn)

        # Count how many leads are waiting in the pool
        unassigned_count = conn.execute(text(
            "SELECT COUNT(*) FROM sales_leads "
            "WHERE status = 'ready_for_swipe' AND assigned_ae_username IS NULL"
        )).scalar()

    no_aes = users_df.empty
    user_list = users_df['username'].tolist() if not no_aes else ["No AEs found"]

    st.caption(f"Unassigned leads ready for distribution: **{unassigned_count}**")

    col_a, col_b, col_c = st.columns([2, 2, 1])
    with col_a:
        selected_ae = st.selectbox("Select Account Executive", user_list, disabled=no_aes)
    with col_b:
        num_leads = st.number_input("Number of Leads", min_value=1, max_value=500, value=10)
    with col_c:
        st.markdown("<br>", unsafe_allow_html=True)  # Aligns the button with the inputs
        # disabled=no_aes stops the placeholder text "No AEs found" from
        # ever being saved into the database as if it were a real person.
        if st.button("Assign Leads", type="primary", use_container_width=True, disabled=no_aes):
            assigned = matchmaker2.assign_leads_to_ae(selected_ae, num_leads)
            if assigned == 0:
                st.error("No unassigned leads available in the pool!")
            else:
                st.success(f"Successfully assigned {assigned} leads to {selected_ae}!")
                st.rerun()  # Refreshes the page to update the metrics

    st.divider()

    # --- SECTION 2: LIVE METRICS ---
    st.markdown("### 📊 Pipeline Health")

    # One trip to the database instead of two: both counts come back
    # from a single query.
    with engine.connect() as conn:
        counts = conn.execute(text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status != 'sourced') AS enriched
            FROM sales_leads
        """)).fetchone()

    total_leads = counts.total or 0
    enriched_leads = counts.enriched or 0
    enriched_pct = round((enriched_leads / total_leads) * 100, 1) if total_leads > 0 else 0.0

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
            LEFT JOIN sales_leads s ON u.username = s.assigned_ae_username
            WHERE u.role != 'admin'
            GROUP BY u.username
            ORDER BY "Processed" DESC
        """)
        with engine.connect() as conn:
            user_stats_df = pd.read_sql(user_stats_query, conn)
        user_stats_df = user_stats_df.fillna(0).astype(
            {'Total Assigned': 'int', 'Processed': 'int', 'Pending': 'int'}
        )
        st.dataframe(user_stats_df, hide_index=True, use_container_width=True)

    with col_data:
        st.markdown("### 🏢 Latest Leads Preview")
        leads_preview_query = text("""
            SELECT
                company_name AS "Company",
                status AS "Status",
                assigned_ae_username AS "Assigned To"
            FROM sales_leads
            ORDER BY created_at DESC
            LIMIT 100
        """)
        with engine.connect() as conn:
            leads_preview_df = pd.read_sql(leads_preview_query, conn)
        st.dataframe(leads_preview_df, hide_index=True, use_container_width=True)