import bcrypt  # pip install bcrypt — add to requirements.txt
import hmac
import time

import streamlit as st
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, Date, Boolean, DateTime, select, update, delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

# Single source of truth: matchmaker2 owns the engine, the connector,
# and the sales_leads schema. app.py no longer duplicates any of it.
import matchmaker2
from matchmaker2 import sales_leads, ml_pipeline_analytics
import admin_panel
import ae_dashboard

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Matchmaker 2.0 | Lead Triage", layout="centered")

engine = matchmaker2.get_backend_engine()

# users isn't defined in matchmaker2, so it lives here — attached to the
# shared metadata so there's still only one MetaData object in play.
users_table = Table(
    'users', matchmaker2.metadata,
    Column('id', Integer, primary_key=True),
    Column('username', String),
    Column('password', String),
    Column('role', String),
    extend_existing=True,
)
# ml_pipeline_analytics and sales_leads are both owned by matchmaker2 and
# imported above. Only users_table is defined locally, so create_all here
# picks that up. It's safe: it only creates tables that don't exist yet.
matchmaker2.metadata.create_all(engine)

# --- SESSION STATE INITIALIZATION ---
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

# --- AUTH ---
def verify_password(stored: str, supplied: str) -> bool:
    """Checks a password. Understands both scrambled (bcrypt) passwords
    and old plain-text ones, so nobody is locked out during the changeover."""
    if not stored:
        return False
    if stored.startswith("$2"):  # bcrypt hashes always start with this
        return bcrypt.checkpw(supplied.encode(), stored.encode())
    return hmac.compare_digest(stored, supplied)  # legacy plain text

def upgrade_password_to_hash(user_id: int, plain_password: str):
    """Quietly converts an old plain-text password to a scrambled one
    the first time that user logs in successfully. This means the whole
    team migrates to secure passwords automatically — no script needed."""
    hashed = bcrypt.hashpw(plain_password.encode(), bcrypt.gensalt()).decode()
    with engine.begin() as conn:
        conn.execute(
            update(users_table)
            .where(users_table.c.id == user_id)
            .values(password=hashed)
        )

# --- DATA LOADING ---
@st.cache_data(ttl=600)
def get_pending_leads(ae_username):
    """Fetches this AE's unprocessed leads, best score first.
    Cached so the database isn't queried on every single click."""
    with engine.connect() as conn:
        query = (
            select(sales_leads)
            .where(
                (sales_leads.c.status == 'ready_for_swipe')
                & (sales_leads.c.assigned_ae_username == ae_username)
            )
            .order_by(sales_leads.c.confidence_score.desc())
        )
        return [dict(row) for row in conn.execute(query).mappings().fetchall()]

# Feature engineering lives in matchmaker2 (shared with My Pipeline);
# reference it as matchmaker2.engineer_ml_features.

# ==========================================
# PAGE 1: THE LOGIN PORTAL
# ==========================================
def login_page():
    st.title("🔒 Matchmaker Login")
    with st.form("login_form"):
        input_username = st.text_input("Username").strip().lower()
        input_password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Log In")

        if submit:
            with engine.connect() as conn:
                query = select(users_table).where(
                    users_table.c.username == input_username
                )
                user_record = conn.execute(query).fetchone()

            if user_record and verify_password(user_record.password, input_password):
                # If they logged in with an old plain-text password,
                # upgrade it to a secure scrambled one right now.
                if not user_record.password.startswith("$2"):
                    upgrade_password_to_hash(user_record.id, input_password)

                st.session_state.logged_in = True
                st.session_state.username = user_record.username
                st.session_state.role = user_record.role
                st.rerun()
            else:
                st.error("Invalid username or password. Please try again.")

# ==========================================
# PAGE 2: THE MAIN SWIPING APP
# ==========================================
def main_app():
    st.title("🔥 Matchmaker 2.0 Triage")
    st.write("Review your assigned leads. Validate the data, add context, and submit.")
    st.divider()

    leads = get_pending_leads(st.session_state.username)

    if not leads:
        st.success("🎉 Inbox Zero! You've triaged all your assigned leads.")
        if st.button("Check for New Leads"):
            get_pending_leads.clear()
            st.rerun()
        return

    current_lead = leads[0]

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
                web_valid = st.checkbox("Website is accurate", value=True, key="web_val")
            else:
                st.markdown("**🌐 Website:** ❌ Not Found")
                web_valid = False

        with col2:
            if current_lead['linkedin_url']:
                st.markdown(f"**💼 LinkedIn:** [View Profile]({current_lead['linkedin_url']})")
                li_valid = st.checkbox("LinkedIn is accurate", value=True, key="li_val")
            else:
                st.markdown("**💼 LinkedIn:** ❌ Not Found")
                li_valid = False

        st.divider()

        # --- THE DECISION ENGINE ---
        st.markdown("### Pipeline Decision")

        col_pass, col_approve = st.columns(2)

        with col_pass:
            # Two-step pass: only ask for the reason *after* the AE commits to
            # archiving, so the reason dropdown doesn't distract during review.
            if st.session_state.get('pending_pass') == current_lead['id']:
                rejection_reason = st.selectbox(
                    "Why are you passing?",
                    ["Bad Industry", "Too Small", "No Public Info", "Competitor", "Out of Business", "Other"],
                    key="rej_reason"
                )
                confirm, cancel = st.columns(2)
                if confirm.button("Confirm Archive", type="primary", use_container_width=True):
                    dwell_time = int(time.time() - st.session_state.start_time)
                    age_months, dir_count = matchmaker2.engineer_ml_features(current_lead)

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
                    st.session_state.pending_pass = None
                    get_pending_leads.clear()
                    st.rerun()
                if cancel.button("Cancel", use_container_width=True):
                    st.session_state.pending_pass = None
                    st.rerun()
            else:
                if st.button("❌ Pass (Archive)", use_container_width=True):
                    st.session_state.pending_pass = current_lead['id']
                    st.rerun()

        with col_approve:
            # Approve is a fast yes: mark it approved and stash the validation
            # checkboxes onto the lead. CRM classification happens later in
            # "My Pipeline", where the ML row gets written.
            if st.button("✅ Approve", type="primary", use_container_width=True):
                with engine.begin() as conn:
                    conn.execute(
                        update(sales_leads).where(sales_leads.c.id == current_lead['id'])
                        .values(
                            status='approved',
                            website_accurate=web_valid,
                            linkedin_accurate=li_valid,
                        )
                    )
                st.session_state.pending_pass = None
                get_pending_leads.clear()
                st.rerun()

    st.caption(f"{len(leads)} lead{'s' if len(leads) != 1 else ''} left to review")

# ==========================================
# ROUTING LOGIC
# ==========================================
if not st.session_state.logged_in:
    login_page()
else:
    with st.sidebar:
        st.write(
            f"Logged in as: **{st.session_state.username.title()}** "
            f"({st.session_state.role.title()})"
        )

        nav_options = ["Swipe Leads", "My Pipeline"]
        if st.session_state.role == 'admin':
            nav_options.append("Admin Dashboard")
        page_selection = st.radio("Navigation", nav_options)

        st.divider()

        if st.button("Log Out"):
            st.session_state.clear()
            st.rerun()

    if page_selection == "Swipe Leads":
        main_app()
    elif page_selection == "My Pipeline":
        ae_dashboard.render_ae_pipeline(engine, st.session_state.username)
    elif page_selection == "Admin Dashboard":
        if st.session_state.role == 'admin':  # check at the door, not just hide the button
            admin_panel.render_dashboard(engine)
        else:
            st.error("You don't have permission to view this page.")