# Deploying the React rebuild

Two services, deployed separately:

- **API** (FastAPI, `api/`) → **Railway** (this guide), as a **Docker** service so it
  installs only `api/requirements.txt` (Starlette 0.41 / no Streamlit) and can't
  collide with the worker's Streamlit deps.
- **Frontend** (Next.js, `frontend/`) → **Vercel** — already set up; just needs the
  API's URL.

The Streamlit app (`main` branch) and the existing Railway worker (also `main`) are
untouched — this only adds a new service that deploys from `react-rebuild`.

---

## 1. API on Railway

The API build is defined by **`Dockerfile.api`** + **`railway.api.json`** — named
so they are NOT auto-detected repo-wide. This matters because the worker builds
from the same repo: a plain root `Dockerfile`/`railway.json` would be applied to
the worker too and break it (no Streamlit, plus a `/health` check it can't answer).
The worker keeps building with Nixpacks + the root `requirements.txt`, untouched.

1. Railway → your existing project → **New → GitHub Repo** → `Jaceur/MatchMaker2`.
   This creates a **new, separate service** — do not reconfigure the worker.
2. Open the new service → **Settings**:
   - **Source → Branch:** `react-rebuild`; **Root Directory:** *(leave blank — repo
     root; the build needs the shared modules that live there)*
   - **Config-as-code → Railway Config File:** set the path to **`railway.api.json`**.
     (This is the key step — it tells *this* service to use `Dockerfile.api`.)
3. **Variables** tab → add:

   | Key | Value |
   |---|---|
   | `DB_PASSWORD` | *(your Supabase DB password, from `.streamlit/secrets.toml`)* |
   | `SUPABASE_HOST` | `aws-0-eu-west-1.pooler.supabase.com` |
   | `SUPABASE_USER` | `postgres.grkwrvxerhyvusuvprmu` |
   | `SUPABASE_PORT` | `5432` |
   | `SUPABASE_DBNAME` | `postgres` |
   | `CH_API_KEY` | *(Companies House REST key, from `.streamlit/secrets.toml`)* — needed for director enrichment |
   | `JWT_SECRET` | *(a long random string — `python -c "import secrets;print(secrets.token_urlsafe(32))"`)* |
   | `CORS_ORIGINS` | *(your Vercel URL, e.g. `https://your-app.vercel.app`)* |

4. **Settings → Networking → Generate Domain.** Railway gives you a
   `https://<service>.up.railway.app` URL. (It injects `$PORT`; the Dockerfile
   already listens on it, so the domain wires up automatically.)
5. Verify: open `https://<service>.up.railway.app/health` → `{"status":"ok"}`, and
   `…/docs` for the interactive API.

Unlike a free Render service, this stays warm — no cold-start delay.

### The existing worker
The `lead_worker` service needs **no changes**: it builds with Nixpacks from the
root `requirements.txt` (which includes Streamlit) and runs `python lead_worker.py`
via the `Procfile`. Because the API build files use non-default names, the worker
is unaffected whether it deploys from `main` or `react-rebuild`. If a bad build
already broke it, just **redeploy** it after this change and it recovers.

---

## 2. Point the frontend at the API (Vercel)
1. Vercel project → **Settings → Environment Variables** → add
   `NEXT_PUBLIC_API_URL` = `https://<service>.up.railway.app` (no trailing slash).
2. **Redeploy** the frontend (env vars only apply to new builds).

## 3. Close the CORS loop
On Railway, make sure `CORS_ORIGINS` contains the exact Vercel URL. Add any custom
domain later too (comma-separated). Railway redeploys on variable changes.

Then log in on the Vercel URL — the swipe queue should load from Supabase.

---

### Troubleshooting
- **Build fails on a `pip` wheel** (e.g. `pandas`): bump the base image in
  `Dockerfile` from `python:3.12-slim` to `python:3.13-slim` and redeploy.
- **"Application failed to respond"**: the app isn't listening on `$PORT` — confirm
  the domain was generated and the deploy is "Active", and check the deploy logs.
- **Login works but data calls fail with a CORS error** (browser console): the
  Vercel origin isn't in `CORS_ORIGINS` exactly (scheme + host, no trailing slash).
