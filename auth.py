"""Authentication: password checking, transparent hash upgrades, login page."""
import time

import bcrypt  # pip install bcrypt — add to requirements.txt
import hmac

import streamlit as st
from sqlalchemy import select, update

from database import engine
from models import users_table

MAX_LOGIN_ATTEMPTS = 5      # failed tries before a temporary lockout
LOCKOUT_SECONDS = 60        # how long the lockout lasts


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


def migrate_plaintext_passwords():
    """Bcrypt-hash any password still stored as plain text, in place. Idempotent
    (rows already starting with '$2' are skipped) and non-disruptive — each
    user's existing password keeps working. Run once at startup so no cleartext
    lingers at rest waiting for that user to next log in. Returns count migrated."""
    migrated = 0
    with engine.begin() as conn:
        rows = conn.execute(
            select(users_table.c.id, users_table.c.password)
        ).fetchall()
        for row in rows:
            pw = row.password
            if pw and not pw.startswith("$2"):
                hashed = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
                conn.execute(
                    update(users_table)
                    .where(users_table.c.id == row.id)
                    .values(password=hashed)
                )
                migrated += 1
    return migrated


def login_page():
    st.title("🔒 Matchmaker Login")

    # Temporary lockout after too many failed attempts (per session).
    locked_for = int(st.session_state.get('login_locked_until', 0) - time.time())
    if locked_for > 0:
        st.error(f"Too many failed attempts. Try again in {locked_for}s.")

    with st.form("login_form"):
        input_username = st.text_input("Username").strip().lower()
        input_password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Log In")

        if submit and locked_for <= 0:
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

                st.session_state.login_attempts = 0
                st.session_state.pop('login_locked_until', None)
                st.session_state.logged_in = True
                st.session_state.username = user_record.username
                st.session_state.role = user_record.role
                st.rerun()
            else:
                attempts = st.session_state.get('login_attempts', 0) + 1
                if attempts >= MAX_LOGIN_ATTEMPTS:
                    st.session_state.login_locked_until = time.time() + LOCKOUT_SECONDS
                    st.session_state.login_attempts = 0
                    st.error(f"Too many failed attempts. Locked for {LOCKOUT_SECONDS}s.")
                else:
                    st.session_state.login_attempts = attempts
                    st.error(
                        "Invalid username or password. "
                        f"{MAX_LOGIN_ATTEMPTS - attempts} attempt(s) left."
                    )
