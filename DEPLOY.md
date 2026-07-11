# Deploying the React rebuild

Two services, deployed separately:

- **API** (FastAPI, `api/`) → **Render** (this guide) — its own environment because
  it needs Starlette 0.41 / no Streamlit, which conflicts with the worker's deps.
- **Frontend** (Next.js, `frontend/`) → **Vercel** — already set up; just needs the
  API's URL.

The Streamlit app (`main` branch) and the Railway worker are untouched.

---

## 1. API on Render

### Option A — Blueprint (uses `render.yaml`, recommended)
1. Push the `react-rebuild` branch (so `render.yaml` is on GitHub).
2. Render dashboard → **New → Blueprint** → connect `Jaceur/MatchMaker2` → pick the
   **`react-rebuild`** branch. Render reads `render.yaml` and creates `matchmaker-api`.
3. It'll prompt for the `sync: false` env vars — fill them in (see the table below).
4. **Create** → first deploy runs.

### Option B — Manual web service
Render → **New → Web Service** → connect the repo, branch `react-rebuild`, then:
- **Root Directory:** *(leave blank — repo root)*
- **Runtime:** Python
- **Build Command:** `pip install -r api/requirements.txt`
- **Start Command:** `uvicorn api.main:app --host 0.0.0.0 --port $PORT`
- **Health Check Path:** `/health`

### Environment variables (both options)
| Key | Value | Notes |
|---|---|---|
| `DB_PASSWORD` | *(your Supabase DB password)* | same as `.streamlit/secrets.toml` |
| `SUPABASE_HOST` | `aws-0-eu-west-1.pooler.supabase.com` | Session pooler host |
| `SUPABASE_USER` | `postgres.grkwrvxerhyvusuvprmu` | Session pooler user |
| `SUPABASE_PORT` | `5432` | |
| `SUPABASE_DBNAME` | `postgres` | |
| `JWT_SECRET` | *(a long random string)* | signs login tokens; set a real one |
| `CORS_ORIGINS` | `https://<your-app>.vercel.app` | your Vercel URL (comma-separate several) |
| `PYTHON_VERSION` | `3.12.8` | pinned for dependable wheels |

When it's live, check `https://<service>.onrender.com/health` → `{"status":"ok"}`,
and `…/docs` for the interactive API.

> **Free plan note:** the service sleeps after ~15 min idle, so the first request
> after a pause takes ~30–60s to wake. Fine for now; upgrade later if it annoys.

---

## 2. Point the frontend at the API (Vercel)
1. Vercel project → **Settings → Environment Variables** → add
   `NEXT_PUBLIC_API_URL` = `https://<service>.onrender.com` (no trailing slash).
2. **Redeploy** the frontend (env vars only apply to new builds).

## 3. Close the CORS loop
Back on Render, make sure `CORS_ORIGINS` contains the exact Vercel URL. If you add a
custom domain later, add it here too (comma-separated). Redeploy the API after changes.

That's it — log in on the Vercel URL and the swipe queue should load from Supabase.
