"""Load the project's `.env` into the process environment.

ONE place decides where secrets come from. Import this before anything reads
`os.environ` for a credential. Only two modules read secrets — `database.py`
(the Supabase connection) and `ch_client.py` (the Companies House keys) — and
both import this, so every entry point is covered: the API, the Railway worker,
and the local scripts (`rerun_pipeline.py`, `enrich_local.py`, `sic_data.py`, …).

Replaces the old split where the API read `api/.env` while everything else read
`.streamlit/secrets.toml` via `st.secrets` — a Streamlit-shaped mechanism that
outlived Streamlit itself, and meant a rotated password had to be pasted in two
places (it wasn't, which is how it broke).

On Railway there is no `.env` file: the platform sets real environment variables
and `load_dotenv` simply no-ops. Real env vars also WIN over the file, because
load_dotenv defaults to `override=False` — so a local `.env` can never shadow
production config.
"""
import os

from dotenv import load_dotenv

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

load_dotenv(ENV_PATH)
