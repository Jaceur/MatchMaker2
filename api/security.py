"""Authentication: password checking, JWT issue/verify, and the FastAPI
dependency that turns a Bearer token back into the current user.

Replaces the Streamlit cookie/HMAC session with stateless token auth. Passwords
still live in the shared `users` table (bcrypt, with the same legacy-plaintext
tolerance the Streamlit app had), so no user migration is needed.
"""
import hmac
import time

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select

from database import engine
from models import users_table

from .config import settings

# tokenUrl is only used by the OpenAPI docs' "Authorize" button.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


# ==========================================
# PASSWORDS  (mirrors the old auth.verify_password)
# ==========================================
def verify_password(stored: str, supplied: str) -> bool:
    """Check a password against the stored value. Understands both bcrypt hashes
    and old plain-text passwords, so nobody is locked out during changeover."""
    if not stored:
        return False
    if stored.startswith("$2"):  # bcrypt hashes always start with this
        return bcrypt.checkpw(supplied.encode(), stored.encode())
    return hmac.compare_digest(stored, supplied)  # legacy plain text


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


# ==========================================
# JWT
# ==========================================
def create_access_token(username: str, role: str) -> str:
    now = int(time.time())
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + settings.access_token_expire_minutes * 60,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


class CurrentUser:
    """The authenticated user, extracted from a valid token."""

    def __init__(self, username: str, role: str):
        self.username = username
        self.role = role


def get_current_user(token: str = Depends(oauth2_scheme)) -> CurrentUser:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError:
        raise credentials_exception
    username = payload.get("sub")
    role = payload.get("role")
    if not username:
        raise credentials_exception
    return CurrentUser(username=username, role=role)


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Dependency for admin-only endpoints: check at the door, not just in the UI."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


# ==========================================
# LOGIN
# ==========================================
def authenticate(username: str, password: str) -> CurrentUser | None:
    """Verify credentials against the users table. Upgrades a legacy plain-text
    password to bcrypt on the way in (same behaviour as the old login page).
    Returns the user on success, None on failure."""
    from sqlalchemy import update

    username = username.strip().lower()
    with engine.connect() as conn:
        row = conn.execute(
            select(users_table).where(users_table.c.username == username)
        ).fetchone()
    if not row or not verify_password(row.password, password):
        return None
    if not row.password.startswith("$2"):
        with engine.begin() as conn:
            conn.execute(
                update(users_table)
                .where(users_table.c.id == row.id)
                .values(password=hash_password(password))
            )
    return CurrentUser(username=row.username, role=row.role)
