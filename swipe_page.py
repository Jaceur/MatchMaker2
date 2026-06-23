"""The swipe page: review one assigned lead at a time, validate, Pass or Approve.

Self-contained Streamlit view. Rendered by app.py via main_app().
"""
import time

import streamlit as st
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import engine
from models import sales_leads, ml_pipeline_analytics
from leads import get_pending_leads, build_ml_row, award_activity


@st.fragment
def validity_toggle(field_key, lead_id, label):
    """A tick/cross pair the AE taps to mark a source correct or incorrect.
    Wrapped in a fragment so tapping it (or typing a correction) reruns only this
    control — the lead card and decision panel stay put. State is keyed per lead
    in session_state; read it back elsewhere with _validity()/_corrected().
    Defaults to Correct, since most scraped sources are right. Marking it
    Incorrect opens a box to paste the right URL."""
    state_key = f"{field_key}_{lead_id}"
    if state_key not in st.session_state:
        st.session_state[state_key] = True  # default: Correct

    current = st.session_state[state_key]
    c_ok, c_no = st.columns(2)
    # st.rerun() here is fragment-scoped (we're inside @st.fragment), so it only
    # repaints the toggle — not the whole page.
    if c_ok.button("✅ Correct", key=f"{state_key}_ok", use_container_width=True,
                   type="primary" if current else "secondary"):
        st.session_state[state_key] = True
        st.rerun()
    if c_no.button("❌ Incorrect", key=f"{state_key}_no", use_container_width=True,
                   type="primary" if not current else "secondary"):
        st.session_state[state_key] = False
        st.rerun()

    # When marked Incorrect, let the AE supply the correct URL.
    if not st.session_state[state_key]:
        st.text_input(
            f"Correct {label} URL",
            key=f"{field_key}_corrected_{lead_id}",
            placeholder="https://...",
        )


@st.fragment
def correction_input(field_key, lead_id, label):
    """Standalone box for a source the scraper couldn't find at all. Shares the
    same session key as validity_toggle's correction box, so whatever the AE
    types flows through _corrected()/_corrected_values() unchanged. (A missing
    source already counts as 'not accurate' — _validity returns False when no
    toggle was shown.) Fragment-wrapped so typing doesn't reload the page."""
    st.text_input(
        f"Add correct {label} URL",
        key=f"{field_key}_corrected_{lead_id}",
        placeholder="https://...",
    )


def _validity(field_key, lead_id):
    """Read a validity toggle's current value. Defaults to False when the toggle
    was never shown (e.g. a lead with no website — nothing to vouch for). When
    the toggle *is* shown it initialises itself to True, so an untouched source
    reads as Correct."""
    return st.session_state.get(f"{field_key}_{lead_id}", False)


def _corrected(field_key, lead_id):
    """The AE-entered correct URL — but only when the source was marked Incorrect
    (None otherwise, including when the box is left blank)."""
    if _validity(field_key, lead_id):
        return None
    val = st.session_state.get(f"{field_key}_corrected_{lead_id}")
    return val.strip() if val and val.strip() else None


def _corrected_values(lead_id):
    """Corrected-URL columns to APPLY to sales_leads — only for sources the AE
    actually re-typed this session. Omitting a column preserves any existing
    correction instead of nulling it (matters when a passed lead is re-handed)."""
    out = {}
    web = _corrected("web_val", lead_id)
    if web is not None:
        out["corrected_website_url"] = web
    li = _corrected("li_val", lead_id)
    if li is not None:
        out["corrected_linkedin_url"] = li
    return out


PASS_REASONS = [
    "None Selected", "Bad Industry", "Too Small", "No Public Info",
    "Competitor", "Out of Business", "Other",
]


def _refill_lead_queue():
    """Pull this AE's pending leads into a session-held queue. Advancing to the
    next lead then just pops the queue locally — no DB round-trip per swipe, so
    the next lead appears instantly. We only re-query when the queue runs dry."""
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

        web_valid = _validity("web_val", current_lead['id'])
        li_valid = _validity("li_val", current_lead['id'])
        dwell_time = int(time.time() - st.session_state.start_time)
        corrected = _corrected_values(current_lead['id'])

        with engine.begin() as conn:
            # 1. Update live pipeline
            conn.execute(
                update(sales_leads).where(sales_leads.c.id == current_lead['id'])
                .values(
                    status='archived',
                    rejection_reason=rejection_reason,
                    **corrected,
                )
            )
            # 2. Log ML Data
            conn.execute(
                pg_insert(ml_pipeline_analytics).values(**build_ml_row(
                    current_lead, st.session_state.username,
                    website_valid=web_valid, linkedin_valid=li_valid,
                    corrected_website_url=_corrected("web_val", current_lead['id']) or current_lead.get('corrected_website_url'),
                    corrected_linkedin_url=_corrected("li_val", current_lead['id']) or current_lead.get('corrected_linkedin_url'),
                    is_worth_it=False, rejection_reason=rejection_reason,
                    dwell_time_seconds=dwell_time,
                ))
            )
            # 3. Award leaderboard points (1 swipe + any URLs the AE added)
            award_activity(conn, st.session_state.username,
                           urls_added=len(corrected), leads_swiped=1)
        # Advance locally — instant, no DB re-query — then refresh the whole page.
        st.session_state.lead_queue.pop(0)
        st.rerun(scope="app")


def main_app():
    st.title("🔥 Matchmaker 2.0 Triage")
    st.write("Review your assigned leads. Validate the data, add context, and submit.")
    st.divider()

    # Load once on entry, and top up from the DB only when we've worked through
    # the local queue.
    if 'lead_queue' not in st.session_state or not st.session_state.lead_queue:
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
        accounts = current_lead.get('account_type') or "—"
        st.caption(
            f"Status: Active | Incorporated: {current_lead['incorporation_date']} "
            f"| Accounts: {accounts}"
        )
        if current_lead.get('director_change_recent'):
            st.warning(f"🔄 Recent director change ({current_lead.get('last_director_change')})")

        score = current_lead.get('confidence_score') or 0
        st.progress(score / 100, text=f"Data Confidence Score: {score}%")

        # --- QUICK LINKS & VALIDATION ---
        st.markdown("### Source Links & Validation")
        col1, col2 = st.columns(2)

        with col1:
            # Prefer a previously-supplied correction (e.g. from when this lead
            # was passed and is now being re-handed) over the scraped URL.
            website = current_lead.get('corrected_website_url') or current_lead['website_url']
            if website:
                note = " ✏️ *corrected*" if current_lead.get('corrected_website_url') else ""
                st.markdown(f"**🌐 Website:** [Visit Site]({website}){note}")
                validity_toggle("web_val", current_lead['id'], "Website")
            else:
                st.markdown("**🌐 Website:** ❌ Not Found")
                correction_input("web_val", current_lead['id'], "Website")

        with col2:
            linkedin = current_lead.get('corrected_linkedin_url') or current_lead['linkedin_url']
            if linkedin:
                note = " ✏️ *corrected*" if current_lead.get('corrected_linkedin_url') else ""
                st.markdown(f"**💼 LinkedIn:** [View Profile]({linkedin}){note}")
                validity_toggle("li_val", current_lead['id'], "LinkedIn")
            else:
                st.markdown("**💼 LinkedIn:** ❌ Not Found")
                correction_input("li_val", current_lead['id'], "LinkedIn")

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
                corrected = _corrected_values(current_lead['id'])
                with engine.begin() as conn:
                    conn.execute(
                        update(sales_leads).where(sales_leads.c.id == current_lead['id'])
                        .values(
                            status='approved',
                            website_accurate=_validity("web_val", current_lead['id']),
                            linkedin_accurate=_validity("li_val", current_lead['id']),
                            **corrected,
                        )
                    )
                    # Award leaderboard points (1 swipe + any URLs the AE added)
                    award_activity(conn, st.session_state.username,
                                   urls_added=len(corrected), leads_swiped=1)
                # Advance locally — instant, no DB re-query.
                st.session_state.lead_queue.pop(0)
                st.rerun()

    st.caption(
        f"{len(st.session_state.lead_queue)} lead"
        f"{'s' if len(st.session_state.lead_queue) != 1 else ''} left to review"
    )
