"""The swipe page: review one assigned lead at a time, validate, Pass or Approve.

Self-contained Streamlit view. Rendered by app.py via main_app().
"""
import time

import streamlit as st
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import engine
from models import sales_leads, ml_pipeline_analytics
from leads import get_pending_leads, engineer_ml_features


@st.fragment
def validity_toggle(field_key, lead_id):
    """A tick/cross pair the AE taps to mark a source correct or incorrect.
    Wrapped in a fragment so tapping it reruns only this control — the lead card
    and decision panel stay put. State is keyed per lead in session_state; read
    it back elsewhere with _validity(). Defaults to Incorrect — that's the more
    common verdict, so the common path is zero clicks."""
    state_key = f"{field_key}_{lead_id}"
    if state_key not in st.session_state:
        st.session_state[state_key] = False  # default: Incorrect

    current = st.session_state[state_key]
    c_ok, c_no = st.columns(2)
    # st.rerun() here is fragment-scoped (we're inside @st.fragment), so it only
    # repaints the toggle to update the highlight — not the whole page.
    if c_ok.button("✅ Correct", key=f"{state_key}_ok", use_container_width=True,
                   type="primary" if current else "secondary"):
        st.session_state[state_key] = True
        st.rerun()
    if c_no.button("❌ Incorrect", key=f"{state_key}_no", use_container_width=True,
                   type="primary" if not current else "secondary"):
        st.session_state[state_key] = False
        st.rerun()


def _validity(field_key, lead_id):
    """Read a validity toggle's current value (False if it was never shown/set,
    e.g. a lead with no website)."""
    return st.session_state.get(f"{field_key}_{lead_id}", False)


PASS_REASONS = [
    "None Selected", "Bad Industry", "Too Small", "No Public Info",
    "Competitor", "Out of Business", "Other",
]


def _refill_lead_queue():
    """Pull this AE's pending leads into a session-held queue. Advancing to the
    next lead then just pops the queue locally — no DB round-trip per swipe, so
    the next lead appears instantly. We only re-query when the queue runs dry."""
    get_pending_leads.clear()
    st.session_state.lead_queue = list(get_pending_leads(st.session_state.username))


@st.fragment
def pass_control(current_lead):
    """Pass panel: reason dropdown + archive button. Changing the dropdown reruns
    only this fragment (the lead card above stays put). Committing the pass
    escalates to a full-app rerun so the next lead loads."""
    rejection_reason = st.selectbox(
        "Reason for passing", PASS_REASONS, key=f"rej_{current_lead['id']}"
    )
    if st.button("❌ Pass (Archive)", use_container_width=True):
        if rejection_reason == "None Selected":
            st.warning("Pick a reason before passing.")
            return

        score = current_lead.get('confidence_score') or 0
        web_valid = _validity("web_val", current_lead['id'])
        li_valid = _validity("li_val", current_lead['id'])
        dwell_time = int(time.time() - st.session_state.start_time)
        age_months, dir_count = engineer_ml_features(current_lead)

        with engine.begin() as conn:
            # 1. Update live pipeline
            conn.execute(
                update(sales_leads).where(sales_leads.c.id == current_lead['id'])
                .values(status='archived', rejection_reason=rejection_reason)
            )
            # 2. Log ML Data
            conn.execute(
                pg_insert(ml_pipeline_analytics).values(
                    lead_id=current_lead['id'], crn=current_lead['crn'],
                    company_age_months=age_months, director_count=dir_count,
                    website_score=score, linkedin_score=score, overall_score=score,
                    website_valid=web_valid, linkedin_valid=li_valid,
                    is_worth_it=False, rejection_reason=rejection_reason,
                    dwell_time_seconds=dwell_time, swiped_by=st.session_state.username
                )
            )
        # Advance locally — instant, no DB re-query — then refresh the whole page.
        st.session_state.lead_queue.pop(0)
        get_pending_leads.clear()
        st.rerun(scope="app")


def main_app():
    st.title("🔥 Matchmaker 2.0 Triage")
    st.write("Review your assigned leads. Validate the data, add context, and submit.")
    st.divider()

    # Load once on entry, and top up from the DB only when we've worked through
    # the local queue.
    if 'lead_queue' not in st.session_state:
        _refill_lead_queue()
    if not st.session_state.lead_queue:
        _refill_lead_queue()

    if not st.session_state.lead_queue:
        st.success("🎉 Inbox Zero! You've triaged all your assigned leads.")
        if st.button("Check for New Leads"):
            _refill_lead_queue()
            st.rerun()
        return

    current_lead = st.session_state.lead_queue[0]

    # --- THE HIDDEN DWELL TIMER ---
    # Start the clock the moment a new lead appears on the screen
    if 'current_lead_id' not in st.session_state or st.session_state.current_lead_id != current_lead['id']:
        st.session_state.start_time = time.time()
        st.session_state.current_lead_id = current_lead['id']

    with st.container(border=True):
        st.subheader(f"🏢 {current_lead['company_name']}")
        st.caption(f"Status: Active | Incorporated: {current_lead['incorporation_date']}")

        score = current_lead.get('confidence_score') or 0
        st.progress(score / 100, text=f"Data Confidence Score: {score}%")

        # --- QUICK LINKS & VALIDATION ---
        st.markdown("### Source Links & Validation")
        col1, col2 = st.columns(2)

        with col1:
            if current_lead['website_url']:
                st.markdown(f"**🌐 Website:** [Visit Site]({current_lead['website_url']})")
                validity_toggle("web_val", current_lead['id'])
            else:
                st.markdown("**🌐 Website:** ❌ Not Found")

        with col2:
            if current_lead['linkedin_url']:
                st.markdown(f"**💼 LinkedIn:** [View Profile]({current_lead['linkedin_url']})")
                validity_toggle("li_val", current_lead['id'])
            else:
                st.markdown("**💼 LinkedIn:** ❌ Not Found")

        st.divider()

        # --- THE DECISION ENGINE ---
        st.markdown("### Pipeline Decision")

        col_pass, col_approve = st.columns(2)

        with col_pass:
            # Reason dropdown + archive button live in a fragment, so picking a
            # reason doesn't reload the whole lead view.
            pass_control(current_lead)

        with col_approve:
            # Approve is a fast yes: mark it approved and stash the validation
            # toggles onto the lead, then advance immediately. It changes the page
            # (next lead), so it stays a normal full-app button — not a fragment.
            st.markdown("&nbsp;")  # spacer to align the button with Pass
            if st.button("✅ Approve", type="primary", use_container_width=True):
                with engine.begin() as conn:
                    conn.execute(
                        update(sales_leads).where(sales_leads.c.id == current_lead['id'])
                        .values(
                            status='approved',
                            website_accurate=_validity("web_val", current_lead['id']),
                            linkedin_accurate=_validity("li_val", current_lead['id']),
                        )
                    )
                # Advance locally — instant, no DB re-query.
                st.session_state.lead_queue.pop(0)
                get_pending_leads.clear()
                st.rerun()

    st.caption(
        f"{len(st.session_state.lead_queue)} lead"
        f"{'s' if len(st.session_state.lead_queue) != 1 else ''} left to review"
    )
