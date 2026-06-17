"""Matchmaker 2.0 — Streamlit entry point.

Thin master: page config, session bootstrap, and routing. All real work lives
in the page modules (swipe_page, ae_dashboard, admin_panel) and the backend
modules they import.
"""
import time

import streamlit as st
import extra_streamlit_components as stx

from database import engine
from auth import (
    login_page, migrate_plaintext_passwords,
    restore_session, refresh_auth_cookie, clear_auth_cookie,
)
import swipe_page
import ae_dashboard
import admin_panel
import leaderboard

# --- PAGE CONFIGURATION (must be the first st command) ---
st.set_page_config(page_title="Matchmaker 2.0 | Lead Triage", layout="centered")


# Purge any cleartext passwords once per process (cache_resource = runs on first
# boot only). Wrapped so a migration hiccup can never block the login screen.
@st.cache_resource
def _secure_passwords_once():
    try:
        return migrate_plaintext_passwords()
    except Exception as e:
        print(f"Password migration skipped: {e}")
        return 0


_secure_passwords_once()

# Cookie-backed session: survives a page refresh and returns within the 10-min
# idle window. The manager renders a small component, so it must come after
# set_page_config.
cookie_manager = stx.CookieManager()

# --- SESSION STATE INITIALIZATION ---
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

# A fresh connection (refresh / reopen) wipes session_state — rebuild it from
# the auth cookie if one is still valid.
if not st.session_state.logged_in:
    restore_session(cookie_manager)

# ==========================================
# ROUTING LOGIC
# ==========================================
if not st.session_state.logged_in:
    login_page(cookie_manager)
elif time.time() > st.session_state.get('auth_exp', 0):
    # Hard idle timeout: the window lapsed (e.g. tab left open and idle), so log
    # out on this interaction. Checked BEFORE the sliding refresh below.
    clear_auth_cookie(cookie_manager)
    st.session_state.clear()
    st.rerun()
else:
    refresh_auth_cookie(cookie_manager)  # slide the 10-min idle window forward
    with st.sidebar:
        st.write(
            f"Logged in as: **{st.session_state.username.title()}** "
            f"({st.session_state.role.title()})"
        )

        nav_options = ["Swipe Leads", "My Pipeline", "Leaderboard"]
        if st.session_state.role == 'admin':
            nav_options.append("Admin Dashboard")
        page_selection = st.radio("Navigation", nav_options)

        st.divider()

        if st.button("Log Out"):
            clear_auth_cookie(cookie_manager)
            st.session_state.clear()
            st.rerun()

    if page_selection == "Swipe Leads":
        swipe_page.main_app()
    elif page_selection == "My Pipeline":
        ae_dashboard.render_ae_pipeline(engine, st.session_state.username)
    elif page_selection == "Leaderboard":
        leaderboard.render_leaderboard(engine)
    elif page_selection == "Admin Dashboard":
        if st.session_state.role == 'admin':  # check at the door, not just hide the button
            admin_panel.render_dashboard(engine)
        else:
            st.error("You don't have permission to view this page.")
