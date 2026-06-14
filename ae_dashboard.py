import streamlit as st
import pandas as pd
from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

# matchmaker2 owns the schema + shared feature engineering. ae_dashboard
# imports from it (never from mmapp) to avoid a circular import.
import matchmaker2
from matchmaker2 import sales_leads, ml_pipeline_analytics

CRM_STATUS_OPTIONS = ["Net New", "Existing Lead", "Existing Account", "NAB", "Disqualified"]


# Cached at module level so reruns (every button click, every widget interaction)
# don't hammer the DB. The leading underscore on _engine tells Streamlit not to
# try hashing the engine object — only ttl + username invalidate the cache.
@st.cache_data(ttl=300)
def get_unclassified_leads(_engine, username: str):
    """Approved leads that still need a CRM status — i.e. no ML row written yet.
    These come straight off the swipe page's 'Approve' action."""
    query = text("""
        SELECT id, crn, company_name, incorporation_date, active_directors,
               confidence_score, website_url, linkedin_url,
               website_accurate, linkedin_accurate
        FROM sales_leads sl
        WHERE assigned_ae_username = :username
          AND status = 'approved'
          AND NOT EXISTS (
              SELECT 1 FROM ml_pipeline_analytics m WHERE m.lead_id = sl.id
          )
        ORDER BY updated_at DESC
    """)
    with _engine.connect() as conn:
        return [dict(r) for r in conn.execute(query, {"username": username}).mappings().fetchall()]


@st.cache_data(ttl=300)
def get_classified_leads(_engine, username: str) -> pd.DataFrame:
    """Approved leads that already have a CRM status (and an ML row)."""
    query = text("""
        SELECT
            sl.company_name      AS "Company",
            sl.confidence_score  AS "Match %",
            sl.website_url       AS "Website",
            sl.linkedin_url      AS "LinkedIn",
            m.crm_status         AS "CRM Status",
            sl.is_nabd           AS "NAB'd",
            DATE(sl.updated_at)  AS "Date Approved"
        FROM sales_leads sl
        JOIN ml_pipeline_analytics m ON m.lead_id = sl.id
        WHERE sl.assigned_ae_username = :username
          AND sl.status = 'approved'
        ORDER BY sl.updated_at DESC
    """)
    with _engine.connect() as conn:
        return pd.read_sql(query, conn, params={"username": username})


def classify_lead(engine, lead: dict, crm_status: str, username: str):
    """Commit an AE's CRM-status decision: write the ML training row and
    flag NAB on the live lead. This is the deferred half of 'Approve'."""
    score = lead.get('confidence_score') or 0
    age_months, dir_count = matchmaker2.engineer_ml_features(lead)

    with engine.begin() as conn:
        # 1. Log ML Data (dwell isn't captured here — it's a swipe-screen metric)
        conn.execute(
            pg_insert(ml_pipeline_analytics).values(
                lead_id=lead['id'], crn=lead['crn'],
                company_age_months=age_months, director_count=dir_count,
                website_score=score, linkedin_score=score, overall_score=score,
                website_valid=lead['website_accurate'], linkedin_valid=lead['linkedin_accurate'],
                is_worth_it=True, crm_status=crm_status,
                dwell_time_seconds=None, swiped_by=username
            )
        )
        # 2. Reflect NAB on the live pipeline row
        conn.execute(
            update(sales_leads).where(sales_leads.c.id == lead['id'])
            .values(is_nabd=(crm_status == 'NAB'))
        )
    get_unclassified_leads.clear()
    get_classified_leads.clear()


@st.fragment
def _classify_card(engine, lead: dict, username: str):
    """One approved lead awaiting a CRM status. Picking a status reruns only this
    card (not the other cards, the metrics, or the summary table below); saving
    escalates to a full-app rerun so the lead drops off the list."""
    with st.container(border=True):
        st.markdown(f"**🏢 {lead['company_name']}**  ·  Match {lead.get('confidence_score') or 0}%")

        links = []
        if lead['website_url']:
            links.append(f"[🌐 Website]({lead['website_url']})")
        if lead['linkedin_url']:
            links.append(f"[💼 LinkedIn]({lead['linkedin_url']})")
        if links:
            st.markdown("  ·  ".join(links))

        col_sel, col_btn = st.columns([3, 1])
        crm_status = col_sel.selectbox(
            "CRM status", CRM_STATUS_OPTIONS,
            key=f"crm_{lead['id']}", label_visibility="collapsed"
        )
        if col_btn.button("Save", key=f"save_{lead['id']}", type="primary", use_container_width=True):
            classify_lead(engine, lead, crm_status, username)
            st.rerun(scope="app")


def render_ae_pipeline(engine, current_username: str):
    st.title("🚀 My Approved Pipeline")
    st.write("Set the CRM status for newly approved leads, then track your pipeline.")
    st.divider()

    # --- STEP 1: CLASSIFY NEWLY APPROVED LEADS ---
    pending = get_unclassified_leads(engine, current_username)
    if pending:
        st.subheader(f"📥 Needs CRM Status ({len(pending)})")
        st.caption("Classify each approved lead — this is what writes its ML training row.")

        for lead in pending:
            _classify_card(engine, lead, current_username)

        st.divider()

    # --- STEP 2: CLASSIFIED PIPELINE SUMMARY ---
    df = get_classified_leads(engine, current_username)

    if df.empty:
        if not pending:
            st.info("You haven't approved any leads yet. Get swiping!")
        if st.button("Refresh"):
            get_unclassified_leads.clear()
            get_classified_leads.clear()
            st.rerun()
        return

    st.subheader("✅ Classified Pipeline")

    # Lightweight summary row — cheap because it reuses the cached frame,
    # no extra queries.
    col1, col2, col3 = st.columns(3)
    col1.metric("Classified Leads", len(df))
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
        get_unclassified_leads.clear()
        get_classified_leads.clear()
        st.rerun()
