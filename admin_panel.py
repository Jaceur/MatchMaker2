import streamlit as st
import pandas as pd
from sqlalchemy import text

from leads import clear_all_data, clear_pipeline_data, top_up_allocation
from settings import (
    get_qualify_percent, set_qualify_percent, qualify_percent_to_bar, get_qualify_bar,
)


def _save_qualify_bar():
    """Persist the qualification slider when it moves, and refresh the cached
    pipeline metrics so they reflect the new bar (on_change callback)."""
    set_qualify_percent(st.session_state.qualify_slider)
    _get_pipeline_stats.clear()


# ==========================================
# CACHED METRICS READS
# ==========================================
# These power the dashboard's read-only panels. Cached so the page doesn't
# re-query the DB on every button click / rerun; the pipeline actions below call
# _clear_admin_caches() so the numbers refresh immediately after a mutation.
@st.cache_data(ttl=120)
def _get_pipeline_stats(_engine):
    """All Pipeline Health counts in one round-trip. 'Screened out' = leads the
    staged pipeline eliminated; 'Qualified' = ready-to-swipe leads that clear the
    current fit bar (the admin slider)."""
    bar = get_qualify_bar()
    query = text("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE status = 'screened_out') AS screened_out,
            COUNT(*) FILTER (WHERE status = 'sourced') AS awaiting_enrichment,
            COUNT(*) FILTER (
                WHERE status = 'ready_for_swipe' AND lead_score >= :bar
            ) AS qualified,
            COUNT(*) FILTER (
                WHERE status = 'ready_for_swipe'
                  AND assigned_ae_username IS NULL AND lead_score >= :bar
            ) AS awaiting_allocation,
            AVG(lead_score) FILTER (
                WHERE status = 'ready_for_swipe' AND lead_score >= :bar
            ) AS avg_qualified
        FROM sales_leads
    """)
    with _engine.connect() as conn:
        row = conn.execute(query, {"bar": bar}).mappings().fetchone()

    stats = dict(row) if row else {}
    return {
        "total": stats.get('total') or 0,
        "screened_out": stats.get('screened_out') or 0,
        "awaiting_enrichment": stats.get('awaiting_enrichment') or 0,
        "qualified": stats.get('qualified') or 0,
        "awaiting_allocation": stats.get('awaiting_allocation') or 0,
        "avg_qualified": float(stats.get('avg_qualified') or 0),
        "bar": bar,
    }


@st.cache_data(ttl=120)
def _get_ae_performance(_engine):
    """Per-AE workload. 'Total Assigned' counts only LIVE leads (still-to-swipe +
    processed); screened-out / un-enriched leads don't count and shouldn't carry a
    name. Pending = still to swipe; Approved = sent to My Pipeline; Passed = swiped
    away (archived)."""
    query = text("""
        SELECT
            u.username AS "AE Name",
            SUM(CASE WHEN s.status IN ('ready_for_swipe','approved','archived') THEN 1 ELSE 0 END) AS "Total Assigned",
            SUM(CASE WHEN s.status = 'ready_for_swipe' THEN 1 ELSE 0 END) AS "Pending",
            SUM(CASE WHEN s.status = 'approved' THEN 1 ELSE 0 END) AS "Approved",
            SUM(CASE WHEN s.status = 'archived' THEN 1 ELSE 0 END) AS "Passed"
        FROM users u
        LEFT JOIN sales_leads s ON u.username = s.assigned_ae_username
        GROUP BY u.username
        ORDER BY "Pending" DESC
    """)
    with _engine.connect() as conn:
        df = pd.read_sql(query, conn)
    cols = ['Total Assigned', 'Pending', 'Approved', 'Passed']
    return df.fillna(0).astype({c: 'int' for c in cols})


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
    _get_pipeline_stats.clear()
    _get_ae_performance.clear()
    _get_leads_preview.clear()


def render_dashboard(engine):
    st.title("⚙️ Admin Control Center")
    st.write("Manage the Matchmaker 2.0 pipeline engine and monitor team output.")

    stats = _get_pipeline_stats(engine)

    # --- SCREENED-OUT QUICK PANEL ---
    with st.container(border=True):
        st.metric(
            "🗂️ Screened-out leads (eliminated by the pipeline)", stats['screened_out'],
            help="Leads the staged pipeline judged below the qualification bar and set "
                 "to 'screened_out'. Kept in the database for review/training, not sent to AEs.",
        )

    st.divider()

    # --- LEAD QUALIFICATION BAR (admin-tunable) ---
    with st.container(border=True):
        st.markdown("### 🎚️ Lead Qualification Bar")
        if "qualify_slider" not in st.session_state:
            st.session_state.qualify_slider = get_qualify_percent()
        pct = st.slider(
            "How selective should the pipeline be?",
            min_value=0, max_value=100, step=5, format="%d%%",
            key="qualify_slider", on_change=_save_qualify_bar,
            help="0% lets most real companies through (score bar 30/100); "
                 "100% keeps only the strongest (bar 70/100). 50% is the default.",
        )
        bar = qualify_percent_to_bar(pct)
        with engine.connect() as conn:
            counts = conn.execute(text(
                "SELECT COUNT(*) FILTER (WHERE lead_score IS NOT NULL) AS scored, "
                "COUNT(*) FILTER (WHERE lead_score >= :bar) AS passing "
                "FROM sales_leads"
            ), {"bar": bar}).mappings().fetchone()
        scored = (counts or {}).get("scored") or 0
        passing = (counts or {}).get("passing") or 0
        st.caption(
            f"Leads must score **≥ {bar}/100** to reach AEs ({pct}% → bar {bar}). "
            f"Right now **{passing} of {scored}** scored leads clear this bar "
            "— lead scores update as leads are enriched."
        )

    st.divider()

    # --- CLOUD SOURCING & ENRICHMENT (Railway lead worker) ---
    st.markdown("### ☁️ Cloud Sourcing & Enrichment")
    st.caption(
        "Queues a job for the always-on Railway worker: it sources the requested "
        "number of new leads (a fresh **random incorporation date per batch of "
        "100** — same behaviour as the manual button, just repeated) and then "
        "enriches every sourced lead. Close the tab, come back later — it keeps "
        "going in the cloud."
    )
    with engine.connect() as conn:
        job = conn.execute(text(
            "SELECT * FROM pipeline_jobs ORDER BY id DESC LIMIT 1"
        )).mappings().fetchone()

    if job and job["status"] in ("pending", "running"):
        if job["status"] == "pending":
            st.info(f"Job #{job['id']} queued — waiting for the Railway worker "
                    "to pick it up (checks every ~10s).")
        else:
            st.info(f"Job #{job['id']} running on Railway.")
        requested = job["requested"] or 1
        sourced = job["sourced"] or 0
        st.progress(min(sourced / requested, 1.0),
                    text=f"Sourcing: {sourced}/{requested} new leads")
        to_enrich = job["to_enrich"] or 0
        if to_enrich:
            enriched = job["enriched"] or 0
            st.progress(min(enriched / to_enrich, 1.0),
                        text=f"Enriching: {enriched}/{to_enrich} leads")
        col_r, col_c = st.columns(2)
        if col_r.button("🔄 Refresh status", use_container_width=True):
            _clear_admin_caches()
            st.rerun()
        if col_c.button("🛑 Cancel job", use_container_width=True):
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE pipeline_jobs SET status = 'cancelled', "
                    "updated_at = now() WHERE id = :i "
                    "AND status IN ('pending', 'running')"), {"i": job["id"]})
            st.rerun()
    else:
        if job:
            icon = {"done": "✅", "failed": "❌",
                    "cancelled": "⚠️"}.get(job["status"], "ℹ️")
            st.caption(f"{icon} Last job #{job['id']} ({job['status']}): "
                       f"{job['message'] or 'no summary'}")
        col_n, col_b = st.columns([1, 1])
        with col_n:
            n_leads = st.number_input("Leads to source", min_value=1,
                                      max_value=10000, value=500, step=100)
        with col_b:
            st.markdown("<br>", unsafe_allow_html=True)  # align with the input
            if st.button("🚀 Source & enrich in the cloud", type="primary",
                         use_container_width=True):
                with engine.begin() as conn:
                    conn.execute(text(
                        "INSERT INTO pipeline_jobs (job_type, requested, status, "
                        "requested_by, created_at, updated_at) VALUES "
                        "('source_enrich', :n, 'pending', :u, now(), now())"
                    ), {"n": int(n_leads),
                        "u": st.session_state.get("username", "")})
                _clear_admin_caches()
                st.rerun()

    st.divider()

    # --- SECTION 1: THE PIPELINE CONTROLS ---
    st.markdown("### 🛠️ Data Pipeline Operations")
    st.caption(
        "Sourcing + enrichment run via the **Cloud Sourcing & Enrichment** job "
        "above (or locally with `python enrich_local.py`)."
    )

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

    # --- SECTION 1.6: TEAM TOP-UP ALLOCATION ---
    st.markdown("### 🔝 Team Top-Up Allocation")
    st.caption(
        "Fill every AE (and admin) back up to a target number of **pending** leads "
        "— each person receives (target − their current pending). Leads are shared "
        "out best-fit first and **weighted by leaderboard standing**, so higher-ranked "
        "reps get a higher average lead score (admins count as top-ranked). "
        f"Qualified pool available: **{stats['awaiting_allocation']}**."
    )
    colt1, colt2 = st.columns([1, 1])
    with colt1:
        topup_target = st.number_input(
            "Target pending per person", min_value=1, max_value=100, value=20,
        )
    with colt2:
        st.markdown("<br>", unsafe_allow_html=True)  # aligns button with the input
        if st.button("⬆️ Top up the team", type="primary", use_container_width=True):
            with st.spinner("Distributing leads across the team..."):
                summary = top_up_allocation(int(topup_target))
            _clear_admin_caches()
            if summary:
                total = sum(r["Assigned"] for r in summary)
                st.success(f"Topped up {len(summary)} people with {total} leads.")
                st.dataframe(pd.DataFrame(summary), hide_index=True, use_container_width=True)
            else:
                st.info("Everyone is already at the target, or the qualified pool is empty.")

    st.divider()

    # --- SECTION 2: LIVE METRICS ---
    st.markdown("### 📊 Pipeline Health")
    st.caption(
        f"Leads with a **lead score ≥ {stats['bar']}/100** (the qualification bar) reach "
        "AEs, best fit first. Adjust the bar with the slider above."
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Leads", stats['total'])
    c2.metric("Qualified Leads", stats['qualified'])
    c3.metric("Awaiting Enrichment", stats['awaiting_enrichment'])

    c4, c5 = st.columns(2)
    c4.metric("Avg Lead Score (qualified)", f"{stats['avg_qualified']:.0f}/100")
    c5.metric("Awaiting Allocation", stats['awaiting_allocation'])

    st.markdown("<br>", unsafe_allow_html=True)

    # --- SECTION 3: TEAM PERFORMANCE & DATA TABLES ---
    col_team, col_data = st.columns(2)

    with col_team:
        st.markdown("### 🧑‍💻 AE Performance")
        st.dataframe(_get_ae_performance(engine), hide_index=True, use_container_width=True)

    with col_data:
        st.markdown("### 🏢 Latest Leads Preview")
        st.dataframe(_get_leads_preview(engine), hide_index=True, use_container_width=True)
