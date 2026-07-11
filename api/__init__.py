"""Matchmaker 2.0 API package.

Load api/.env into the process environment as early as possible — before any
submodule (config, security → database) reads os.environ for the DB connection.
In production the host sets real env vars and there's no .env file, which
load_dotenv handles gracefully (it just no-ops).
"""
import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
