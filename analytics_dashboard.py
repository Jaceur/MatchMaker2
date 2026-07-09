"""Analytics dashboard — pipeline insights + a "ready for machine learning yet?"
gauge.

Read-only. Everything here is derived from tables the app already fills as it
runs, so nothing new has to be collected:

  - sales_leads          the working pool + final statuses
  - screening_log        one feature snapshot per lead per pipeline run
  - ml_pipeline_analytics one AE verdict per decided lead (is_worth_it)

The ML seam is scoring.score_lead(); this page answers "do we have enough
labelled data to train a model to replace its rules yet?" A labelled example is
a lead that has BOTH a feature snapshot (screening_log) AND an AE verdict
(ml_pipeline_analytics) — the two halves a model needs. Both tables are durable
(they survive a "Clear Pipeline"), so the counts don't reset when the live pool
is cleared.
"""
import math

import streamlit as st
import pandas as pd
from sqlalchemy import text

# --- When can we start ML? (rules of thumb — tune as you learn) --------------
# A first simple model needs a few hundred labelled examples AND enough of the
# minority class (approvals) to learn from. Below these, the hand-written rules
# in scoring.py are still the better bet.
ML_MIN_LABELLED = 400      # leads with features + an AE verdict
ML_MIN_POSITIVES = 100     # of those, how many the AE approved (the rarer class)


# ==========================================
# CACHED READS
# ==========================================
@st.cache_data(ttl=120)
def _ml_readiness(_engine):
    """The labelled-data counts that decide whether ML is worth starting. All
    from the two durable log tables so a pipeline clear never sets them back."""
    query = text("""
        SELECT
          -- leads with an AE verdict (durable log)
          (SELECT COUNT(DISTINCT lead_id) FROM ml_pipeline_analytics
             WHERE lead_id IS NOT NULL) AS verdicts,
          -- leads with a feature snapshot
          (SELECT COUNT(DISTINCT lead_id) FROM screening_log
             WHERE lead_id IS NOT NULL) AS features,
          -- the trainable set: features AND a verdict, joined by lead_id
          (SELECT COUNT(DISTINCT s.lead_id)
             FROM screening_log s
             JOIN ml_pipeline_analytics m ON m.lead_id = s.lead_id) AS trainable,
          -- of the trainable set, how many the AE approved (is_worth_it)
          (SELECT COUNT(DISTINCT s.lead_id)
             FROM screening_log s
             JOIN ml_pipeline_analytics m ON m.lead_id = s.lead_id
            WHERE m.is_worth_it IS TRUE) AS trainable_positives,
          -- decisions logged in the last 4 weeks (for the "rate" estimate)
          (SELECT COUNT(*) FROM ml_pipeline_analytics
             WHERE created_at > now() - interval '28 days') AS decisions_28d
    """)
    with _engine.connect() as conn:
        row = conn.execute(query).mappings().fetchone()
    return {k: (v or 0) for k, v in dict(row or {}).items()}


@st.cache_data(ttl=120)
def _funnel(_engine):
    """Current pool by status + Won count. (Leads cleared to the archive aren't
    in the live pool, so this is a snapshot of what's here now.)"""
    with _engine.connect() as conn:
        by_status = {row[0]: row[1] for row in conn.execute(text(
            "SELECT status, COUNT(*) FROM sales_leads GROUP BY status"
        ))}
        won = conn.execute(text(
            "SELECT COUNT(*) FROM sales_leads WHERE is_nabd IS TRUE"
        )).scalar() or 0
    return by_status, won


@st.cache_data(ttl=120)
def _screen_reasons(_engine):
    query = text("""
        SELECT COALESCE(screen_reason, '(no reason recorded)') AS reason,
               COUNT(*) AS leads
        FROM sales_leads
        WHERE status = 'screened_out'
        GROUP BY 1 ORDER BY leads DESC LIMIT 12
    """)
    with _engine.connect() as conn:
        return pd.read_sql(query, conn)


@st.cache_data(ttl=120)
def _score_calibration(_engine):
    """Approval rate per lead_score band — does a higher score actually get
    approved more? This is the single best read on whether the score works (and
    later, whether a model beats it). Uses the latest feature snapshot per lead
    joined to its verdict."""
    query = text("""
        WITH latest AS (
            SELECT DISTINCT ON (lead_id) lead_id, lead_score
            FROM screening_log
            WHERE lead_id IS NOT NULL AND lead_score IS NOT NULL
            ORDER BY lead_id, created_at DESC
        ),
        verdict AS (
            SELECT lead_id, BOOL_OR(is_worth_it) AS approved
            FROM ml_pipeline_analytics
            WHERE lead_id IS NOT NULL
            GROUP BY lead_id
        )
        SELECT (l.lead_score / 10) * 10 AS band,
               COUNT(*) AS decided,
               SUM(CASE WHEN v.approved THEN 1 ELSE 0 END) AS approved
        FROM latest l JOIN verdict v ON v.lead_id = l.lead_id
        GROUP BY 1 ORDER BY 1
    """)
    with _engine.connect() as conn:
        return pd.read_sql(query, conn)


@st.cache_data(ttl=120)
def _coverage(_engine):
    """How complete the enrichment is — % of enriched leads that have each
    field. Empty fields are the ML model's blind spots."""
    query = text("""
        SELECT
          COUNT(*)                 AS enriched,
          COUNT(employee_count)    AS employees,
          COUNT(turnover)          AS turnover,
          COUNT(cash_at_bank)      AS cash,
          COUNT(website_url)       AS website,
          COUNT(linkedin_url)      AS linkedin
        FROM sales_leads
        WHERE status <> 'sourced'
    """)
    with _engine.connect() as conn:
        row = conn.execute(query).mappings().fetchone()
    return dict(row or {})


def _clear_caches():
    for fn in (_ml_readiness, _funnel, _screen_reasons, _score_calibration, _coverage):
        fn.clear()


# ==========================================
# THE PAGE
# ==========================================
def render_analytics(engine):
    head_l, head_r = st.columns([4, 1])
    with head_l:
        st.title("📈 Analytics")
    with head_r:
        st.write("")
        if st.button("🔄 Refresh", use_container_width=True):
            _clear_caches()
            st.rerun()

    _render_ml_readiness(engine)
    st.divider()
    _render_funnel(engine)
    st.divider()
    _render_screen_reasons(engine)
    st.divider()
    _render_calibration(engine)
    st.divider()
    _render_coverage(engine)


def _render_ml_readiness(engine):
    st.markdown("### 🤖 Ready for machine learning?")
    st.caption(
        "A model learns from **labelled** leads — ones that have both the "
        "features the pipeline saw and the AE's verdict (approve / pass). "
        f"Rule of thumb for a first simple model: about **{ML_MIN_LABELLED} "
        f"labelled leads** with at least **{ML_MIN_POSITIVES} approvals** (the "
        "rarer class). Until then, the hand-written rules are the better bet."
    )

    r = _ml_readiness(engine)
    trainable = r["trainable"]
    positives = r["trainable_positives"]

    need_total = max(0, ML_MIN_LABELLED - trainable)
    need_pos = max(0, ML_MIN_POSITIVES - positives)

    # Approvals are usually the binding constraint: to gain `need_pos` more
    # approvals you need roughly need_pos / (approval rate) more decided leads.
    approval_rate = positives / trainable if trainable else None
    if need_pos and approval_rate:
        leads_for_pos = math.ceil(need_pos / approval_rate)
    elif need_pos:
        leads_for_pos = need_pos          # no rate yet — floor estimate
    else:
        leads_for_pos = 0
    still_needed = max(need_total, leads_for_pos)

    if still_needed == 0:
        st.success(
            f"✅ You have enough labelled data to train a first model "
            f"({trainable} labelled, {positives} approvals). Next step is the "
            "offline training script."
        )
    else:
        m1, m2 = st.columns(2)
        m1.metric("Labelled leads still needed", f"~{still_needed}")
        if approval_rate is not None:
            m2.metric("Current approval rate", f"{approval_rate * 100:.0f}%")

        # rate-based ETA, if the team has been deciding leads recently
        per_week = r["decisions_28d"] / 4
        if per_week > 0:
            eta = math.ceil(still_needed / per_week)
            st.caption(
                f"At the recent rate of about **{per_week:.0f} decisions/week**, "
                f"that's roughly **{eta} more week(s)** of swiping."
            )
        else:
            st.caption(
                "No swipe decisions in the last 4 weeks — the count only grows as "
                "AEs work through their piles."
            )

    # progress on each of the two gates
    p1 = min(trainable / ML_MIN_LABELLED, 1.0) if ML_MIN_LABELLED else 0
    p2 = min(positives / ML_MIN_POSITIVES, 1.0) if ML_MIN_POSITIVES else 0
    st.progress(p1, text=f"Labelled leads: {trainable} / {ML_MIN_LABELLED}")
    st.progress(p2, text=f"Approvals (minority class): {positives} / {ML_MIN_POSITIVES}")

    # surface a lag between the two halves of a labelled example
    if r["verdicts"] and r["features"] and trainable < min(r["verdicts"], r["features"]):
        st.caption(
            f"ℹ️ {r['features']} leads have features and {r['verdicts']} have a "
            f"verdict, but only {trainable} have **both** (the trainable set). "
            "The gap is leads enriched before they were swiped, or vice versa."
        )


def _render_funnel(engine):
    st.markdown("### 🚦 Lead funnel")
    by_status, won = _funnel(engine)
    total = sum(by_status.values())
    ready = by_status.get("ready_for_swipe", 0)
    approved = by_status.get("approved", 0)
    archived = by_status.get("archived", 0)

    c1, c2, c3 = st.columns(3)
    c1.metric("Sourced (awaiting pipeline)", by_status.get("sourced", 0))
    c2.metric("Screened out", by_status.get("screened_out", 0))
    c3.metric("Ready to swipe", ready)
    c4, c5, c6 = st.columns(3)
    c4.metric("Approved", approved)
    c5.metric("Passed", archived)
    c6.metric("Won", won)

    decided = approved + archived
    if decided:
        st.caption(
            f"Of **{decided}** swiped leads, **{approved}** were approved "
            f"(**{approved / decided * 100:.0f}%** approval rate). "
            f"Total leads in the pool: {total}."
        )


def _render_screen_reasons(engine):
    st.markdown("### 🗂️ Why leads get screened out")
    df = _screen_reasons(engine)
    if df.empty:
        st.info("No leads have been screened out yet.")
        return
    st.caption(
        "The pipeline's most common elimination reasons. A reason dominating "
        "here may mean the qualification bar is too high — lower the slider "
        "before touching the scoring."
    )
    st.bar_chart(df.set_index("reason"), horizontal=True)


def _render_calibration(engine):
    st.markdown("### 🎯 Does the score predict approvals?")
    df = _score_calibration(engine)
    if df.empty:
        st.info(
            "Not enough decided leads yet to compare score against outcome. "
            "This fills in as AEs swipe."
        )
        return
    df["Approval rate %"] = (df["approved"] / df["decided"] * 100).round(0)
    df["Score band"] = df["band"].astype(int).map(lambda b: f"{b}–{b + 9}")
    st.caption(
        "Approval rate for each lead-score band. If the bars climb left-to-right, "
        "the score is doing its job — higher-scored leads really do get approved "
        "more. A flat chart means the score isn't predictive (and is the clearest "
        "sign a trained model would help)."
    )
    st.bar_chart(df.set_index("Score band")["Approval rate %"])
    st.dataframe(
        df.rename(columns={"decided": "Leads decided", "approved": "Approved"})
          [["Score band", "Leads decided", "Approved", "Approval rate %"]],
        hide_index=True, use_container_width=True,
    )


def _render_coverage(engine):
    st.markdown("### 🧩 Enrichment coverage")
    cov = _coverage(engine)
    enriched = cov.get("enriched", 0) or 0
    if not enriched:
        st.info("No enriched leads yet.")
        return
    st.caption(
        f"Of **{enriched}** enriched leads, how many have each field. Sparse "
        "fields are the model's blind spots — worth improving before relying on "
        "them as features."
    )
    fields = [
        ("Employee count", "employees"), ("Turnover", "turnover"),
        ("Cash at bank", "cash"), ("Website", "website"), ("LinkedIn", "linkedin"),
    ]
    rows = [
        {"Field": label,
         "Populated": cov.get(key, 0) or 0,
         "Coverage %": round((cov.get(key, 0) or 0) / enriched * 100)}
        for label, key in fields
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
