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

The repo has a root `Dockerfile` + `railway.json`, so Railway builds the API
deterministically (no Nixpacks, no accidental Streamlit install).

1. Railway → your existing project → **New → GitHub Repo** → `Jaceur/MatchMaker2`.
2. Open the new service → **Settings → Source**:
   - **Branch:** `react-rebuild`
   - **Root Directory:** *(leave blank — repo root; the Dockerfile needs the shared
     modules that live there)*
   - Railway detects the `Dockerfile` automatically (builder shows "Dockerfile").
3. **Variables** tab → add:

   | Key | Value |
   |---|---|
   | `DB_PASSWORD` | *(your Supabase DB password, from `.streamlit/secrets.toml`)* |
   | `SUPABASE_HOST` | `aws-0-eu-west-1.pooler.supabase.com` |
   | `SUPABASE_USER` | `postgres.grkwrvxerhyvusuvprmu` |
   | `SUPABASE_PORT` | `5432` |
   | `SUPABASE_DBNAME` | `postgres` |
   | `JWT_SECRET` | *(a long random string — `python -c "import secrets;print(secrets.token_urlsafe(32))"`)* |
   | `CORS_ORIGINS` | *(your Vercel URL, e.g. `https://your-app.vercel.app`)* |

4. **Settings → Networking → Generate Domain.** Railway gives you a
   `https://<service>.up.railway.app` URL. (It injects `$PORT`; the Dockerfile
   already listens on it, so the domain wires up automatically.)
5. Verify: open `https://<service>.up.railway.app/health` → `{"status":"ok"}`, and
   `…/docs` for the interactive API.

Unlike a free Render service, this stays warm — no cold-start delay.

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
