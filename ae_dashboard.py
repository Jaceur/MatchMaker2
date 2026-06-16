import streamlit as st
import pandas as pd
from sqlalchemy import text, update, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

from models import sales_leads, ml_pipeline_analytics, director_emails
from leads import build_ml_row
from directors import enrich_lead_directors, email_candidates, domain_from_url

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
               directors_enriched, confidence_score, website_score, linkedin_score,
               website_url, linkedin_url, website_accurate, linkedin_accurate,
               corrected_website_url, corrected_linkedin_url
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
            COALESCE(sl.corrected_website_url, sl.website_url)   AS "Website",
            COALESCE(sl.corrected_linkedin_url, sl.linkedin_url) AS "LinkedIn",
            m.crm_status         AS "CRM Status",
            sl.active_directors  AS "Directors",
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


def classify_lead(engine, lead: dict, crm_status: str, username: str, email_rows=None):
    """Commit an AE's CRM-status decision: write the ML training row, flag NAB on
    the live lead, and log the director email-format verdicts. The deferred half
    of 'Approve' — all in one transaction."""
    with engine.begin() as conn:
        # 1. Log ML Data (dwell isn't captured here — it's a swipe-screen metric)
        conn.execute(
            pg_insert(ml_pipeline_analytics).values(**build_ml_row(
                lead, username,
                website_valid=lead['website_accurate'], linkedin_valid=lead['linkedin_accurate'],
                corrected_website_url=lead['corrected_website_url'],
                corrected_linkedin_url=lead['corrected_linkedin_url'],
                is_worth_it=True, crm_status=crm_status,
                dwell_time_seconds=None,
            ))
        )
        # 2. Reflect NAB on the live pipeline row
        conn.execute(
            update(sales_leads).where(sales_leads.c.id == lead['id'])
            .values(is_nabd=(crm_status == 'NAB'))
        )
        # 3. Log every director email candidate with its X/Y verdict
        if email_rows:
            conn.execute(insert(director_emails), email_rows)
    get_unclassified_leads.clear()
    get_classified_leads.clear()


@st.fragment
def _pipeline_gate_card(lead: dict):
    """An approved lead waiting to enter the pipeline. Director enrichment (the
    slow Companies House call) is deferred until the AE clicks Yes — only then is
    the lead pulled into the classify list."""
    with st.container(border=True):
        st.markdown(f"**🏢 {lead['company_name']}**  ·  Match {lead.get('confidence_score') or 0}%")
        st.caption("Ready to add to pipeline?")
        if st.button("✅ Yes, add to pipeline", key=f"add_{lead['id']}",
                     type="primary", use_container_width=True):
            with st.spinner("Fetching directors from Companies House..."):
                enrich_lead_directors(lead['id'], lead['crn'])
            get_unclassified_leads.clear()
            st.rerun(scope="app")


def _gather_email_rows(lead, directors, domain, username):
    """Read each email checkbox back out of session_state into rows for the
    director_emails table — one per (director × pattern), with its X/Y verdict."""
    rows = []
    for i, director in enumerate(directors):
        for pattern, email in email_candidates(director, domain):
            key = f"email_{lead['id']}_{i}_{pattern}"
            rows.append({
                "lead_id": lead['id'], "crn": lead['crn'],
                "director_name": director, "pattern": pattern, "email": email,
                "selected": bool(st.session_state.get(key, False)),
                "swiped_by": username,
            })
    return rows


@st.fragment
def _classify_card(engine, lead: dict, username: str):
    """One approved lead awaiting a CRM status. Picking a status reruns only this
    card (not the other cards, the metrics, or the summary table below); saving
    escalates to a full-app rerun so the lead drops off the list."""
    with st.container(border=True):
        st.markdown(f"**🏢 {lead['company_name']}**  ·  Match {lead.get('confidence_score') or 0}%")

        # Prefer the AE-supplied correction over the scraped URL; ✏️ flags it.
        links = []
        website = lead.get('corrected_website_url') or lead['website_url']
        if website:
            tag = " ✏️" if lead.get('corrected_website_url') else ""
            links.append(f"[🌐 Website{tag}]({website})")
        linkedin = lead.get('corrected_linkedin_url') or lead['linkedin_url']
        if linkedin:
            tag = " ✏️" if lead.get('corrected_linkedin_url') else ""
            links.append(f"[💼 LinkedIn{tag}]({linkedin})")
        if links:
            st.markdown("  ·  ".join(links))

        # Directors + suggested emails. Each email gets an X/Y tick (default X =
        # unticked); the verdicts are logged for later analysis on Save.
        domain = domain_from_url(lead.get('corrected_website_url') or lead.get('website_url'))
        directors = [d.strip() for d in (lead.get('active_directors') or "").split(",") if d.strip()]
        if directors:
            st.markdown("**👤 Directors & suggested emails** _(tick = Y, looks right)_")
            for i, director in enumerate(directors):
                st.markdown(f"• **{director}**")
                cands = email_candidates(director, domain)
                if not cands:
                    st.caption("No website domain — can't suggest emails.")
                for pattern, email in cands:
                    st.checkbox(email, key=f"email_{lead['id']}_{i}_{pattern}", value=False)

        col_sel, col_btn = st.columns([3, 1])
        crm_status = col_sel.selectbox(
            "CRM status", CRM_STATUS_OPTIONS,
            key=f"crm_{lead['id']}", label_visibility="collapsed"
        )
        if col_btn.button("Save", key=f"save_{lead['id']}", type="primary", use_container_width=True):
            email_rows = _gather_email_rows(lead, directors, domain, username)
            classify_lead(engine, lead, crm_status, username, email_rows)
            st.rerun(scope="app")


def render_ae_pipeline(engine, current_username: str):
    st.title("🚀 My Approved Pipeline")
    st.write("Set the CRM status for newly approved leads, then track your pipeline.")
    st.divider()

    # --- STEP 1: CLASSIFY NEWLY APPROVED LEADS ---
    pending = get_unclassified_leads(engine, current_username)
    if pending:
        st.subheader(f"📥 New Approved Leads ({len(pending)})")
        st.caption("Add each lead to your pipeline (enriches directors), then set its CRM status.")

        for lead in pending:
            if lead.get('directors_enriched'):
                _classify_card(engine, lead, current_username)
            else:
                _pipeline_gate_card(lead)

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
