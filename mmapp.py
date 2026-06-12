import streamlit as st
import pandas as pd
from google.oauth2 import service_account
from google.cloud.sql.connector import Connector, IPTypes
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, Boolean, DateTime, select, update, text
import matchmaker2
import admin_panel

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Matchmaker 2.0 | Lead Triage", layout="centered")
# 1. Read the passport from Streamlit Secrets
creds = service_account.Credentials.from_service_account_info(
    st.secrets["gcp_service_account"]
)

# 2. Hand the passport to the Connector
connector = Connector(credentials=creds)

# --- OPTIMIZATION 1: SECURE CONNECTION & CACHE ---
@st.cache_resource
def init_db_connection():

    def getconn():
        # Pulling the password securely from Streamlit Secrets
        conn = connector.connect(
            "enrichmentno:europe-west2:matchmaker-2",
            "pg8000",
            user="postgres",
            password=st.secrets["DB_PASSWORD"], 
            db="sales-pipeline",
            ip_type=IPTypes.PUBLIC
        )
        return conn

    return create_engine("postgresql+pg8000://", creator=getconn, pool_pre_ping=True)

engine = init_db_connection()
metadata = MetaData()

# --- OPTIMIZATION 2: EXPLICIT SCHEMA ---
sales_leads = Table(
    'sales_leads', metadata,
    Column('id', Integer, primary_key=True),
    Column('company_name', String),
    Column('incorporation_date', String),
    Column('website_url', String),
    Column('linkedin_url', String),
    Column('website_accurate', Boolean),
    Column('linkedin_accurate', Boolean),
    Column('rejection_reason', String),
    Column('is_nabd', Boolean),
    Column('active_directors', String),
    Column('status', String),
    Column('assigned_ae', String),
    Column('confidence_score', Integer),
    Column('assigned_date', DateTime),
)

users_table = Table(
    'users', metadata,
    Column('id', Integer, primary_key=True),
    Column('username', String),
    Column('password', String),
    Column('role', String)
)

# --- SESSION STATE INITIALIZATION ---
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

# --- DATA LOADING FUNCTION ---
@st.cache_data(ttl=600)
def get_pending_leads(ae_username):
    with engine.connect() as conn:
        query = select(sales_leads).where(
            (sales_leads.c.status == 'ready_for_swipe') & 
            (sales_leads.c.assigned_ae == ae_username)
        )
        results = conn.execute(query).mappings().fetchall()
        return [dict(row) for row in results]

# --- SWIPE ACTIONS ---
def update_lead_status(lead_id, new_status):
    with engine.begin() as conn:
        stmt = update(sales_leads).where(sales_leads.c.id == lead_id).values(status=new_status)
        conn.execute(stmt)
    st.session_state.current_lead_index += 1

# ==========================================
# PAGE 1: THE LOGIN PORTAL
# ==========================================
def login_page():
    st.title("🔒 Matchmaker Login")
    with st.form("login_form"):
        input_username = st.text_input("Username").strip().lower()
        input_password = st.text_input("Password", type="password").strip()
        submit = st.form_submit_button("Log In")
        
        if submit:
            with engine.connect() as conn:
                query = select(users_table).where(
                    (users_table.c.username == input_username) & 
                    (users_table.c.password == input_password)
                )
                user_record = conn.execute(query).fetchone()
                
            if user_record:
                # Store all user info in memory ONCE to save DB queries
                st.session_state.logged_in = True
                st.session_state.username = user_record.username
                st.session_state.role = user_record.role
                st.session_state.current_lead_index = 0
                st.rerun()
            else:
                st.error("Invalid username or password. Please try again.")

# ==========================================
# PAGE 2: THE MAIN SWIPING APP
# ==========================================
def main_app():
    st.title("🔥 Matchmaker 2.0 Triage")
    st.write("Review your assigned leads. **Approve** sends them to CRM, **Pass** archives them.")
    st.divider()

    leads = get_pending_leads(st.session_state.username)

    if st.session_state.current_lead_index >= len(leads):
        st.success("🎉 Inbox Zero! You've triaged all your assigned leads.")
        if st.button("Refresh My Leads"):
            st.cache_data.clear()
            st.session_state.current_lead_index = 0
            st.rerun()
    else:
        current_lead = leads[st.session_state.current_lead_index]
        
        with st.container(border=True):
            st.subheader(f"🏢 {current_lead['company_name']}")
            st.caption(f"Status: Active | Incorporated: {current_lead['incorporation_date']}")

            score = current_lead.get('confidence_score', 0)
            st.progress(score / 100, text=f"Data Confidence Score: {score}%")
            
            st.markdown("### Quick Links")
            col1, col2 = st.columns(2)
            with col1:
                if current_lead['website_url']:
                    st.markdown(f"**🌐 Website:** [Visit Site]({current_lead['website_url']})")
                else:
                    st.markdown("**🌐 Website:** ❌ Not Found")
                    
            with col2:
                if current_lead['linkedin_url']:
                    st.markdown(f"**💼 LinkedIn:** [View Profile]({current_lead['linkedin_url']})")
                else:
                    st.markdown("**💼 LinkedIn:** ❌ Not Found")
                    
            st.markdown("<br>", unsafe_allow_html=True)

            col_pass, col_approve = st.columns(2)
            with col_pass:
                if st.button("❌ Pass (Archive)", use_container_width=True):
                    update_lead_status(current_lead['id'], 'archived')
                    st.rerun()
                    
            with col_approve:
                if st.button("✅ Approve (Send to CRM)", type="primary", use_container_width=True):
                    update_lead_status(current_lead['id'], 'approved')
                    st.rerun()
                    
        st.caption(f"Lead {st.session_state.current_lead_index + 1} of {len(leads)}")

# ==========================================
# ROUTING LOGIC (DRY Principle Applied)
# ==========================================
if not st.session_state.logged_in:
    login_page()
else:
    # 1. Unified Sidebar Setup
    with st.sidebar:
        st.write(f"Logged in as: **{st.session_state.username.title()}** ({st.session_state.role.title()})")
        
        # Only admins get the navigation radio buttons
        if st.session_state.role == 'admin':
            page_selection = st.radio("Navigation", ["Swipe Leads", "Admin Dashboard"])
        else:
            page_selection = "Swipe Leads" # Forced route for AEs
            
        st.divider()
        
        # Unified Logout Button
        if st.button("Log Out"):
            st.session_state.clear() # Instantly deletes all session variables safely
            st.rerun()

# 2. Render the selected page
    if page_selection == "Swipe Leads":
        main_app()
    elif page_selection == "Admin Dashboard":
        # Call the new file and hand it the database engine!
        admin_panel.render_dashboard(engine)