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
