"""Matchmaker 2.0 API — FastAPI backend for the React frontend.

Thin HTTP layer over the existing business-logic modules (scoring, pipeline,
leads, directors, settings, the ch_* engine). Run from the PROJECT ROOT so the
shared modules import cleanly:

    uvicorn api.main:app --reload

Requires the same DB env vars the workers use (DB_PASSWORD + SUPABASE_*), plus
optionally JWT_SECRET and CORS_ORIGINS — see .env.example at the project root.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import auth, leads, pipeline, me, leaderboard, admin, analytics


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the SIC reference table from data/uk_sic_codes.csv once per process,
    mirroring the Streamlit app's _seed_sic_once. It's an idempotent ~730-row
    upsert, so a deploy is all it takes to pick up an edited CSV — no manual
    step, no local DB credentials. Wrapped so a failure can never block boot:
    sic_data falls back to reading the CSV off disk if the table is unusable."""
    try:
        from sic_data import load_sic_lookup
        print(f"SIC lookup loaded: {load_sic_lookup()} codes.")
    except Exception as e:
        print(f"SIC seed skipped: {e}")
    yield


app = FastAPI(title="Matchmaker 2.0 API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(leads.router)
app.include_router(pipeline.router)
app.include_router(me.router)
app.include_router(leaderboard.router)
app.include_router(admin.router)
app.include_router(analytics.router)


@app.get("/health", tags=["meta"])
def health():
    """Liveness probe (no DB, no auth) for the host platform."""
    return {"status": "ok"}
