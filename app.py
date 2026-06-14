"""Matchmaker 2.0 — Streamlit entry point.

Thin master: page config, session bootstrap, and routing. All real work lives
in the page modules (swipe_page, ae_dashboard, admin_panel) and the backend
modules they import.
"""
import streamlit as st

from database import engine
from auth import login_page
import swipe_page
import ae_dashboard
import admin_panel

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Matchmaker 2.0 | Lead Triage", layout="centered")

# --- SESSION STATE INITIALIZATION ---
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

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
        swipe_page.main_app()
    elif page_selection == "My Pipeline":
        ae_dashboard.render_ae_pipeline(engine, st.session_state.username)
    elif page_selection == "Admin Dashboard":
        if st.session_state.role == 'admin':  # check at the door, not just hide the button
            admin_panel.render_dashboard(engine)
        else:
            st.error("You don't have permission to view this page.")
