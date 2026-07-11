"""API configuration, read from environment variables (or a local .env file).

The backend shares the same database as the Streamlit app — the DB connection
itself is configured in the project-root database.py, which reads DB_PASSWORD and
the SUPABASE_* env vars. This file only holds settings specific to the API layer
(auth token signing, CORS, token lifetime).
"""
import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Where the .env file lives (api/.env). Extra env vars (the SUPABASE_* /
    # DB_PASSWORD ones that database.py reads) are ignored here, not errors.
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(__file__), ".env"),
        extra="ignore",
    )

    # Secret used to sign JWTs. Falls back to DB_PASSWORD so the API works with no
    # extra config (mirrors how the Streamlit app derived its cookie key). Set a
    # dedicated JWT_SECRET in production if you'd rather rotate them separately.
    jwt_secret: str = os.environ.get("JWT_SECRET") or os.environ.get("DB_PASSWORD", "dev-insecure-change-me")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12  # 12 hours

    # Comma-separated list of allowed frontend origins for CORS.
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
