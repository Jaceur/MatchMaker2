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
import ssl

import streamlit as st
from sqlalchemy import create_engine, MetaData
from sqlalchemy.engine import URL


# Streamlit caches this so the whole app shares ONE engine / connection pool,
# even though `engine` is imported across many pages.
@st.cache_resource
def get_backend_engine():
    # Structured fields (not a single URL string) so a password containing
    # symbols like @ : / ? # can't corrupt the connection string — URL.create
    # escapes each part for us. The password stays under the existing
    # DB_PASSWORD secret because auth.py also uses it to sign the login cookie.
    cfg = st.secrets["supabase"]
    url = URL.create(
        "postgresql+pg8000",
        username=cfg["user"],                       # e.g. postgres.<project-ref>
        password=str(st.secrets["DB_PASSWORD"]),    # the Supabase DB password
        host=cfg["host"],                           # e.g. aws-0-<region>.pooler.supabase.com
        port=int(cfg.get("port", 5432)),            # Session pooler = 5432
        database=cfg.get("dbname", "postgres"),
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
