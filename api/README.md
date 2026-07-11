# Matchmaker 2.0 — API (FastAPI)

The reactive-frontend backend. A thin HTTP layer over the **existing** business
logic (`scoring`, `pipeline`, `leads`, `directors`, `settings`, `leaderboard`,
the `ch_*` engine). It imports those project-root modules directly — no logic is
duplicated — and shares the same Supabase database as the Streamlit app.

## Why its own virtual environment

This build of Streamlit needs **Starlette 1.x**; FastAPI needs **Starlette
0.41.x**. They can't live in one environment, so the API runs in its own venv
**without Streamlit installed**. The shared modules import cleanly without it
thanks to optional-import guards in `database.py` and `leaderboard.py`.

## Run it locally

From the **project root** (so `import scoring`, `import leads`, … resolve):

```bash
# one-time setup
python -m venv .venv-api
.venv-api\Scripts\pip install -r api/requirements.txt      # Windows
# .venv-api/bin/pip install -r api/requirements.txt          # macOS/Linux

# configure — copy the example and fill in the Supabase creds
copy api\.env.example api\.env                              # Windows
# cp api/.env.example api/.env                                # macOS/Linux

# run (must be from the project root)
.venv-api\Scripts\python -m uvicorn api.main:app --reload --port 8000
```

- Interactive API docs: <http://localhost:8000/docs>
- Liveness probe (no DB, no auth): <http://localhost:8000/health>

`api/.env` holds the same values as `.streamlit/secrets.toml` (`DB_PASSWORD` +
the `SUPABASE_*` host/user/port/dbname). It is **gitignored** — never commit it.

## Auth

Token-based (JWT), replacing the Streamlit cookie session. Passwords stay in the
shared `users` table (bcrypt, with the same legacy-plaintext tolerance).

1. `POST /auth/login` with `{username, password}` → `{access_token, user}`.
2. Send `Authorization: Bearer <access_token>` on every other request.
3. Admin-only routes (`/admin/*`) additionally require `role == "admin"`.

`JWT_SECRET` defaults to `DB_PASSWORD` if unset (set a dedicated one in prod).

## Endpoint map (by frontend view)

| View | Endpoints |
|---|---|
| Login | `POST /auth/login`, `GET /auth/me` |
| Swipe | `GET /leads/pending`, `POST /leads/{id}/pass`, `POST /leads/{id}/approve` |
| My Pipeline | `GET /pipeline/unclassified`, `POST /pipeline/{id}/enrich-directors`, `GET /pipeline/{id}/email-candidates`, `POST /pipeline/{id}/classify`, `GET /pipeline/classified` |
| AE Dashboard | `GET /me/stats`, `POST /me/change-password` |
| Leaderboard | `GET /leaderboard` |
| Admin | `GET/PUT /admin/settings*`, `POST /admin/allocation/topup`, `POST /admin/pipeline-job`, `GET /admin/pipeline-jobs`, `GET /admin/health`, `GET /admin/leads/latest`, `POST /admin/clear/*` |
| Analytics | _(planned)_ |
| New Incorps | _(planned)_ |

## Deployment

Deploy as its own service (Render / Railway / Fly). Install `api/requirements.txt`,
set the same env vars, start with:

```
uvicorn api.main:app --host 0.0.0.0 --port $PORT
```

Set `CORS_ORIGINS` to the deployed frontend URL(s), comma-separated.
```
```
