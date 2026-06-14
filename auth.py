"""Authentication: password checking, transparent hash upgrades, login page."""
import bcrypt  # pip install bcrypt — add to requirements.txt
import hmac

import streamlit as st
from sqlalchemy import select, update

from database import engine
from models import users_table


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
