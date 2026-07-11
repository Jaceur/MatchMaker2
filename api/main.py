"""Matchmaker 2.0 API — FastAPI backend for the React frontend.

Thin HTTP layer over the existing business-logic modules (scoring, pipeline,
leads, directors, settings, the ch_* engine). Run from the PROJECT ROOT so the
shared modules import cleanly:

    uvicorn api.main:app --reload

Requires the same DB env vars the workers use (DB_PASSWORD + SUPABASE_*), plus
optionally JWT_SECRET and CORS_ORIGINS — see api/.env.example.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import auth, leads, pipeline, me, leaderboard, admin

app = FastAPI(title="Matchmaker 2.0 API", version="0.1.0")

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


@app.get("/health", tags=["meta"])
def health():
    """Liveness probe (no DB, no auth) for the host platform."""
    return {"status": "ok"}
