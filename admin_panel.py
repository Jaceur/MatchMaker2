import streamlit as st
import pandas as pd
from sqlalchemy import text

from sourcing import run_sourcing_pipeline
from enrichment import run_enrichment_pipeline
from leads import clear_all_data, clear_pipeline_data, assign_leads_to_ae


# ==========================================
# CACHED METRICS READS
# ==========================================
# These power the dashboard's read-only panels. Cached so the page doesn't
# re-query the DB on every button click / rerun; the pipeline actions below call
# _clear_admin_caches() so the numbers refresh immediately after a mutation.
@st.cache_data(ttl=120)
def _get_user_list(_engine):
    with _engine.connect() as conn:
        df = pd.read_sql("SELECT username FROM users", conn)
    return df['username'].tolist() if not df.empty else ["No AEs found"]


@st.cache_data(ttl=120)
def _get_pipeline_stats(_engine):
    with _engine.connect() as conn:
        unassigned = conn.execute(text(
            "SELECT COUNT(*) FROM sales_leads "
            "WHERE status = 'ready_for_swipe' AND assigned_ae_username IS NULL"
        )).scalar()
        total = conn.execute(text("SELECT COUNT(*) FROM sales_leads")).scalar()
        enriched = conn.execute(text(
            "SELECT COUNT(*) FROM sales_leads WHERE status != 'sourced'"
        )).scalar()
    return {"unassigned": unassigned, "total": total, "enriched": enriched}


@st.cache_data(ttl=120)
def _get_ae_performance(_engine):
    query = text("""
        SELECT
            u.username AS "AE Name",
            COUNT(s.id) AS "Total Assigned",
            SUM(CASE WHEN s.status IN ('approved', 'archived') THEN 1 ELSE 0 END) AS "Processed",
            SUM(CASE WHEN s.status = 'ready_for_swipe' THEN 1 ELSE 0 END) AS "Pending"
        FROM users u
        LEFT JOIN sales_leads s ON u.username = s.assigned_ae_username
        GROUP BY u.username
    """)
    with _engine.connect() as conn:
        df = pd.read_sql(query, conn)
    return df.fillna(0).astype({'Total Assigned': 'int', 'Processed': 'int', 'Pending': 'int'})


@st.cache_data(ttl=120)
def _get_leads_preview(_engine):
    query = text("""
        SELECT company_name AS "Company", status AS "Status",
               assigned_ae_username AS "Assigned To"
        FROM sales_leads
        ORDER BY created_at DESC
        LIMIT 100
    """)
    with _engine.connect() as conn:
        return pd.read_sql(query, conn)


def _clear_admin_caches():
    """Invalidate every dashboard read after a mutating action."""
    _get_user_list.clear()
    _get_pipeline_stats.clear()
    _get_ae_performance.clear()
    _get_leads_preview.clear()


@st.fragment
def _allocation_controls(user_list, unassigned_count):
    """Lead-assignment controls. Changing the AE or the lead count reruns only
    this fragment, so the heavier metrics / AE-performance / preview queries
    further down the page don't re-run on every tweak. Actually assigning leads
    escalates to a full-app rerun to refresh those metrics."""
    col_a, col_b, col_c = st.columns([2, 2, 1])
    with col_a:
        selected_ae = st.selectbox("Select Account Executive", user_list)
    with col_b:
        num_leads = st.number_input("Number of Leads", min_value=1, max_value=500, value=10)
    with col_c:
        st.markdown("<br>", unsafe_allow_html=True)  # Aligns the button with the inputs
        if st.button("Assign Leads", type="primary", use_container_width=True):
            if unassigned_count == 0:
                st.error("No unassigned leads available in the pool!")
            else:
                assigned = assign_leads_to_ae(selected_ae, num_leads)
                _clear_admin_caches()
                st.success(f"Successfully assigned {assigned} leads to {selected_ae}!")
                st.rerun(scope="app")  # Refresh the page to update the metrics


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
                run_sourcing_pipeline()
            _clear_admin_caches()
            st.success("New leads sourced!")

    with col2:
        if st.button("🧠 Run Enrichment", use_container_width=True):
            progress_bar = st.progress(0.0, text="Starting enrichment...")

            def _on_progress(done, total, company_name):
                progress_bar.progress(
                    done / total if total else 1.0,
                    text=f"Enriched {done}/{total} — {company_name}",
                )

            summary = run_enrichment_pipeline(progress_callback=_on_progress)
            progress_bar.empty()
            _clear_admin_caches()
            st.success(summary)

    with col3:
        if st.button("🛑 Clear Database", use_container_width=True,
                     help="Clears the sourcing/working pool. Approved pipeline leads are preserved."):
            with st.spinner("Clearing working pool..."):
                summary = clear_all_data()
            _clear_admin_caches()
            st.warning(summary)

    # --- CLEAR PIPELINE (destructive — two-step confirmation) ---
    if st.session_state.get("confirm_clear_pipeline"):
        st.error(
            "⚠️ This permanently clears the **approved pipeline**. A copy is kept "
            "in `pipeline_archive`, but it's removed from the live app. Are you sure?"
        )
        confirm, cancel = st.columns(2)
        if confirm.button("Yes, clear the pipeline", type="primary", use_container_width=True):
            with st.spinner("Archiving + clearing pipeline..."):
                summary = clear_pipeline_data()
            _clear_admin_caches()
            st.session_state.confirm_clear_pipeline = False
            st.success(summary)
        if cancel.button("Cancel", use_container_width=True):
            st.session_state.confirm_clear_pipeline = False
            st.rerun()
    else:
        if st.button("🧹 Clear Pipeline", use_container_width=True,
                     help="Archives approved leads to pipeline_archive, then clears them from the live pipeline."):
            st.session_state.confirm_clear_pipeline = True
            st.rerun()

    st.divider()

    # --- SECTION 1.5: MANUAL LEAD ALLOCATION ---
    st.markdown("### 🎯 Manual Lead Allocation")

    user_list = _get_user_list(engine)
    stats = _get_pipeline_stats(engine)

    st.caption(f"Unassigned leads ready for distribution: **{stats['unassigned']}**")

    _allocation_controls(user_list, stats['unassigned'])

    st.divider()

    # --- SECTION 2: LIVE METRICS ---
    st.markdown("### 📊 Pipeline Health")

    total_leads = stats['total']
    enriched_leads = stats['enriched']
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
        st.dataframe(_get_ae_performance(engine), hide_index=True, use_container_width=True)

    with col_data:
        st.markdown("### 🏢 Latest Leads Preview")
        st.dataframe(_get_leads_preview(engine), hide_index=True, use_container_width=True)
