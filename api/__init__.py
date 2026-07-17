"""Matchmaker 2.0 API package.

Load the project's .env into the process environment as early as possible —
before any submodule (config, security → database) reads os.environ for the DB
connection. env_loader lives at the project root and is shared with the worker
and the local scripts, so all three read the SAME file; see env_loader.py.

In production the host sets real env vars and there's no .env file, which
load_dotenv handles gracefully (it just no-ops).
"""
import os
import sys

# The API is imported as `api.main` from the project root, which is normally on
# sys.path already. Be explicit anyway so `uvicorn api.main:app` works no matter
# where it's launched from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import env_loader  # noqa: E402,F401  — side effect: loads .env into os.environ
