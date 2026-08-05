"""API configuration, read from environment variables (or the project's .env).

The DB connection itself is configured in the project-root database.py, which
reads DB_PASSWORD and the SUPABASE_* env vars. This file only holds settings
specific to the API layer (auth token signing, CORS, token lifetime).
"""
import os

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Settings(BaseSettings):
    # The project-root .env (api/__init__.py has already loaded it into the
    # environment; this just makes the dependency explicit). Extra env vars —
    # the SUPABASE_* / DB_PASSWORD ones database.py reads — are ignored here,
    # not errors.
    model_config = SettingsConfigDict(
        env_file=os.path.join(_ROOT, ".env"),
        extra="ignore",
    )

    # Secret used to sign JWTs. Falls back to DB_PASSWORD so the API works with
    # no extra config. Set a dedicated JWT_SECRET in production — otherwise one
    # leaked value opens both the database and every user's session.
    # validate_default so the resolver below runs even when JWT_SECRET is absent.
    jwt_secret: str = Field(default="", validate_default=True)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12  # 12 hours

    # Comma-separated list of allowed frontend origins for CORS.
    cors_origins: str = "http://localhost:3000"

    # Shared secret CHStream sends as the X-Ingest-Key header on POST
    # /new-incorps/ingest. Empty = ingest disabled (returns 503), so the endpoint
    # is never open by accident. Env: NEW_INCORP_INGEST_KEY.
    new_incorp_ingest_key: str = ""

    # Starting capital (£) a new incorporation must be strictly ABOVE to count as
    # high value. Lowered 25,000 -> 10,000 on 2026-08-05. Env-tunable
    # (HIGH_VALUE_CAPITAL_THRESHOLD) so retuning it is a variable change, not a
    # code change — expect to move it again once you see the volume it lets in.
    high_value_capital_threshold: int = 10_000

    # ---- Google Sheet sink (one-way: the API POSTs, it never reads back) ----
    # The Apps Script web-app /exec URL. EMPTY = the whole sink is off (nothing
    # queued, no background task, no calls) — so the sheet is opt-in per
    # environment and a dev API can't spam production's sheet.
    sheet_webhook_url: str = ""
    # Shared secret the Apps Script checks on every POST. A published Apps Script
    # web app is a public URL, so without this anyone who guesses it can write
    # rows. Empty = the sink refuses to send (fail closed, logged once).
    sheet_webhook_key: str = ""
    # Send EVERY incorporation to the sheet, or high-value only. The "all" feed is
    # ~1,500-2,500 rows/day; set SHEET_SEND_ALL=0 to keep the sheet to the
    # high-value ones alone.
    sheet_send_all: bool = True
    # Tab names — must match the sheet exactly (the Apps Script creates them if
    # they're missing, header row included).
    sheet_tab_all: str = "New Incorps"
    sheet_tab_high_value: str = "High Value"
    # How long a row may wait before its batch is sent. **0 = real time**: the
    # flusher is woken the moment the row is queued, no polling delay.
    #
    # The trade-off is Apps Script's DAILY RUNTIME QUOTA (90 min on a personal
    # account, 6 h on Workspace) — each call costs ~1-2s, so one call per row is
    # ~35-70 min/day at 2,000 incorps. High-value is a fraction of that volume, so
    # it runs real-time (2026-08-05); the unfiltered feed stays batched because
    # nobody is racing on it and it's the half that would burn the quota.
    sheet_flush_seconds: int = 15
    sheet_flush_seconds_high_value: int = 0

    @field_validator("jwt_secret")
    @classmethod
    def _resolve_jwt_secret(cls, value: str) -> str:
        """Treat a BLANK JWT_SECRET as "not set" and fall back to DB_PASSWORD.

        `JWT_SECRET=` (declared but empty) is the natural thing to leave in a
        .env, and pydantic-settings honours it as the literal empty string —
        skipping the fallback and handing PyJWT an empty signing key, which it
        rejects outright ("HMAC key must not be empty"), 500-ing every login.
        """
        return value or os.environ.get("DB_PASSWORD") or "dev-insecure-change-me"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
