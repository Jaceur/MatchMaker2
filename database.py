"""Connection layer: the one engine + MetaData the whole app shares.

Every other module imports `engine` and `metadata` from here, so there is a
single connection pool and a single schema registry in play.

The database is hosted on **Supabase** (managed Postgres). Connection details
come from Streamlit secrets (`.streamlit/secrets.toml`), see the [supabase]
section there. Use the **Session pooler** connection info from the Supabase
dashboard (Connect -> Session pooler), NOT the "direct connection": the direct
host is IPv6-only, which Streamlit Cloud can't reach, whereas the pooler is
IPv4-friendly and behaves like a normal Postgres connection.

Still on the pure-Python `pg8000` driver (no binary wheels), so it installs
cleanly on the deploy env's bleeding-edge Python.
"""
import os
import ssl
import functools

# Streamlit is optional: the web app has it, but the FastAPI backend and the
# headless workers import this module without it. When it's absent we fall back
# to environment variables for config and a plain singleton for the engine.
try:
    import streamlit as st
except ImportError:
    # Not installed (the API/workers), or installed but unimportable here (e.g. a
    # Starlette version pinned for FastAPI that this Streamlit build rejects).
    # Either way: treat Streamlit as absent and use env vars + a plain singleton.
    st = None

from sqlalchemy import create_engine, MetaData
from sqlalchemy.engine import URL


def _cache_resource(func):
    """Use Streamlit's cache_resource when running inside Streamlit (so the whole
    app shares one engine across reruns); otherwise a plain lru_cache singleton."""
    if st is not None:
        return st.cache_resource(show_spinner=False)(func)
    return functools.lru_cache(maxsize=1)(func)


def _conn_params():
    """Supabase connection settings, from Streamlit secrets when available (the
    web app) and otherwise from environment variables (a headless host like the
    always-on worker in ch_worker.py, where there's no secrets.toml). The env
    var names mirror the secrets keys: DB_PASSWORD, SUPABASE_HOST/PORT/USER/
    DBNAME."""
    def secret(section, key=None):
        # st.secrets raises if there's no secrets file at all — treat any miss
        # as "not set here, look at the environment instead". Without Streamlit
        # (the API / workers) there are no secrets at all: go straight to env.
        if st is None:
            return None
        try:
            return st.secrets[section][key] if key else st.secrets[section]
        except Exception:
            return None

    password = secret("DB_PASSWORD") or os.environ.get("DB_PASSWORD")
    host = secret("supabase", "host") or os.environ.get("SUPABASE_HOST")
    user = secret("supabase", "user") or os.environ.get("SUPABASE_USER")
    port = secret("supabase", "port") or os.environ.get("SUPABASE_PORT", 5432)
    dbname = secret("supabase", "dbname") or os.environ.get("SUPABASE_DBNAME", "postgres")
    if not (host and user and password):
        raise RuntimeError(
            "Database config missing. Set a [supabase] section + DB_PASSWORD in "
            ".streamlit/secrets.toml, or SUPABASE_HOST/SUPABASE_USER/DB_PASSWORD "
            "environment variables."
        )
    return host, int(port), user, dbname, str(password)


# Streamlit caches this so the whole app shares ONE engine / connection pool,
# even though `engine` is imported across many pages. show_spinner=False keeps
# it quiet when imported OUTSIDE Streamlit (the ch_worker.py headless worker),
# where drawing a spinner has no session and would otherwise log errors.
@_cache_resource
def get_backend_engine():
    # Structured fields (not a single URL string) so a password containing
    # symbols like @ : / ? # can't corrupt the connection string — URL.create
    # escapes each part for us. The password stays under the existing
    # DB_PASSWORD secret because auth.py also uses it to sign the login cookie.
    host, port, user, dbname, password = _conn_params()
    url = URL.create(
        "postgresql+pg8000",
        username=user,          # e.g. postgres.<project-ref>
        password=password,      # the Supabase DB password
        host=host,              # e.g. aws-0-<region>.pooler.supabase.com
        port=port,              # Session pooler = 5432
        database=dbname,
    )

    # Supabase requires TLS, but its pooler cert is rooted in Supabase's own
    # (non-public) CA, so verifying against the system trust store fails with
    # "self-signed certificate in certificate chain". So we ENCRYPT but don't
    # verify the chain — the exact behaviour of Supabase's own `sslmode=require`
    # connection strings, and what their client examples use. (To harden to
    # full verification later: download Supabase's CA cert and load it with
    # ssl.create_default_context(cafile=...); keep check_hostname True.)
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False          # must be cleared before CERT_NONE
    ssl_ctx.verify_mode = ssl.CERT_NONE

    return create_engine(
        url,
        connect_args={"ssl_context": ssl_ctx},
        pool_pre_ping=True,   # test a pooled connection before use (drops stale ones)
        pool_recycle=1800,    # and refresh any held > 30 min, so the pooler's
                              # idle-timeout can't hand us a dead socket
    )


# The shared singletons. Import these — never build your own.
engine = get_backend_engine()
metadata = MetaData()
