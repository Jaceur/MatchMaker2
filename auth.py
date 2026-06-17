"""Authentication: password checking, hash upgrades, login, and a cookie-backed
session so a refresh (or returning within the idle window) keeps you logged in.

The cookie holds a short signed token (HMAC over username/role/expiry). It's a
*session* cookie — gone when the browser closes (best-effort; some browsers keep
session cookies across restarts) — and carries a 10-minute sliding expiry that
is the reliable backstop.
"""
import time
import json
import hmac
import hashlib
import base64

import bcrypt  # pip install bcrypt — add to requirements.txt

import streamlit as st
from sqlalchemy import select, update

from database import engine
from models import users_table

MAX_LOGIN_ATTEMPTS = 5          # failed tries before a temporary lockout
LOCKOUT_SECONDS = 60            # how long the lockout lasts
SESSION_TTL_SECONDS = 600       # sliding 10-minute idle window
COOKIE_NAME = "mm_auth"


# ==========================================
# PASSWORDS
# ==========================================
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
    the first time that user logs in successfully."""
    hashed = bcrypt.hashpw(plain_password.encode(), bcrypt.gensalt()).decode()
    with engine.begin() as conn:
        conn.execute(
            update(users_table)
            .where(users_table.c.id == user_id)
            .values(password=hashed)
        )


def migrate_plaintext_passwords():
    """Bcrypt-hash any password still stored as plain text, in place. Idempotent
    and non-disruptive. Run once at startup so no cleartext lingers at rest.
    Returns count migrated."""
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


# ==========================================
# SIGNED SESSION TOKEN
# ==========================================
def _signing_key() -> bytes:
    # Reuse an existing secret as the HMAC key — no new secret to provision. Set
    # a dedicated AUTH_SIGNING_KEY in secrets if you'd rather separate concerns.
    return str(st.secrets["DB_PASSWORD"]).encode()


def _sign_token(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(_signing_key(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _verify_token(token):
    """Return the payload if the token is well-formed, correctly signed, and not
    past its embedded expiry; otherwise None."""
    if not token:
        return None
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(_signing_key(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(body.encode()).decode())
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


def issue_auth_cookie(cookie_manager, username, role):
    """Write a fresh signed session cookie carrying a 10-minute expiry. Failures
    are swallowed: the in-session login still works, we just lose cross-refresh
    persistence rather than blocking the user."""
    exp = time.time() + SESSION_TTL_SECONDS
    token = _sign_token({"u": username, "r": role, "exp": exp})
    try:
        # expires_at=None -> js-cookie omits expiry -> a session cookie.
        cookie_manager.set(COOKIE_NAME, token, expires_at=None)
    except Exception as e:
        print(f"Could not set auth cookie: {e}")
    st.session_state.auth_exp = exp


def clear_auth_cookie(cookie_manager):
    try:
        cookie_manager.delete(COOKIE_NAME)
    except Exception:
        pass


def restore_session(cookie_manager):
    """If a valid auth cookie exists, restore the login into session_state. Used
    after a refresh, when server-side session_state has been wiped."""
    try:
        token = cookie_manager.get(COOKIE_NAME)
    except Exception:
        token = None
    payload = _verify_token(token)
    if payload:
        st.session_state.logged_in = True
        st.session_state.username = payload["u"]
        st.session_state.role = payload["r"]
        st.session_state.auth_exp = payload["exp"]


def refresh_auth_cookie(cookie_manager):
    """Sliding window: once the token passes its halfway point, re-issue it so an
    actively-working AE never times out. Re-issues at most ~once per 5 minutes."""
    exp = st.session_state.get("auth_exp", 0)
    if exp - time.time() < SESSION_TTL_SECONDS / 2:
        issue_auth_cookie(cookie_manager,
                          st.session_state.username, st.session_state.role)


# ==========================================
# LOGIN PAGE
# ==========================================
def login_page(cookie_manager):
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
                # Upgrade a legacy plain-text password on the way in.
                if not user_record.password.startswith("$2"):
                    upgrade_password_to_hash(user_record.id, input_password)

                st.session_state.login_attempts = 0
                st.session_state.pop('login_locked_until', None)
                st.session_state.logged_in = True
                st.session_state.username = user_record.username
                st.session_state.role = user_record.role
                issue_auth_cookie(cookie_manager, user_record.username, user_record.role)
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
