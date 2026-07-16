# Matchmaker 2.0 — Handover

A lead-triage platform. It sources UK companies from Companies House, runs them through a
**staged enrichment pipeline** (cheap checks first, expensive last), **scores each for sales
fit**, and lets AEs swipe qualified leads Tinder-style — Pass or Approve — then classify
approved leads into CRM statuses. Points/leaderboard + admin control centre + analytics.

> **Read this first.** The stack is **Next.js on Vercel + FastAPI on Railway**, off branch
> `react-rebuild` (the GitHub **default** branch). All work happens here.
>
> **The legacy Streamlit app is RETIRED — no longer deployed anywhere (as of 2026-07-16).**
> Its files are still in the tree (`app.py`, `*_page.py`, `lead_card.py`, `admin_panel.py`,
> `ae_*.py`, `auth.py`, `analytics_dashboard.py`) and the root `requirements.txt` still carries
> Streamlit because **the Railway worker builds from it** — so don't rip Streamlit out of the root
> requirements without re-testing the worker. See §10 for the cutover cleanup this now unblocks.
>
> **The immediate next task is SIC-code screening — see §9 before anything else.**

---

## 1. Infra & deployment (react-rebuild stack)

| Piece | Where | Notes |
|---|---|---|
| **Frontend** (`frontend/`) | **Vercel**, builds `react-rebuild` | Project setting **Root Directory = `frontend`**. Env var `NEXT_PUBLIC_API_URL` = the Railway API URL (must include `https://` — without a scheme the browser treats it as a relative path; `api.ts` now normalises this). |
| **API** (`api/`) | **Railway** service "MatchMaker2" — `matchmaker2-production.up.railway.app` | **Docker** build via `Dockerfile.api` + `railway.api.json` (set in service Settings → Config-as-code). Deliberately NOT named `Dockerfile`/`railway.json` — a default-named file would also be applied to the worker and break it. Env vars: `DB_PASSWORD`, `SUPABASE_HOST/PORT/USER/DBNAME`, **`CH_API_KEY`** (without it director enrichment silently returns "No directors found"), `JWT_SECRET`, `CORS_ORIGINS` (exact Vercel origin, no trailing slash), `PORT=8000`. Health: `/health`; docs: `/docs`. |
| **Worker** (`lead_worker.py`) | **Railway** service "worker" | Nixpacks + root `requirements.txt` + `Procfile` (`worker: python lead_worker.py`). Drains `pipeline_jobs` (the admin "Gather & enrich" button). Needs to run code that includes the candidates feature (commit `23f696b`+) for new leads to get candidate lists. |
| ~~**Legacy Streamlit**~~ | — | **Retired 2026-07-16.** No longer deployed. Page files still in the tree pending cleanup (§10). |
| **DB** | Supabase Postgres, Session pooler + `pg8000` | Connection in `database.py` (secrets.toml locally, env vars headless). |
| **CHStream** | separate repo `Jaceur/CHStream`, own Railway service | Not part of this repo anymore. |

**Local dev:** API needs its own venv (`.venv-api`) because **the Streamlit in the root
`requirements.txt` needs Starlette 1.x and FastAPI needs 0.41.x — they cannot share an
environment**. (Still true even though the Streamlit *app* is retired: the root requirements
are what the Railway worker builds from, so Streamlit is still installed there.) `pip install -r api/requirements.txt` into `.venv-api`, then from the repo root: `.venv-api\Scripts\python -m uvicorn api.main:app --reload --port 8000`. Frontend: `cd frontend && npm run dev`. Secrets: `api/.env` (gitignored, mirrors secrets.toml). Full walkthroughs in `api/README.md` and `DEPLOY.md`.

## 2. Architecture (file map)

Shared Python modules at repo root (imported by the API **and** the worker — `database`, `models`,
`settings`, `scoring`, `pipeline`, `enrichment`, `second_enrichment`, `leads`, `directors`,
`sourcing`, `leaderboard`, `sic_data` + the `ch_*` engine). `database.py` / `leaderboard.py` /
`sic_data.py` import Streamlit *optionally* (try/except → `st = None`) so the API venv works
without it; they fall back to env vars + `lru_cache` in place of `st.secrets` + `st.cache_*`.

| New file | Responsibility |
|---|---|
| `api/main.py` | FastAPI app + CORS + router registration |
| `api/security.py` | JWT auth (reuses `users` table + bcrypt, legacy-plaintext upgrade on login) |
| `api/services.py` | Streamlit-free pass/approve/classify transactions (mirror the old page logic) |
| `api/routers/` | `auth, leads (swipe), pipeline (classify), me, leaderboard, admin, analytics` |
| `api/analytics.py` | pandas computations for the analytics board |
| `frontend/src/lib/` | `api.ts` (fetch + token), `auth.tsx` (context), `types.ts`, `format.ts` |
| `frontend/src/components/` | `SwipeCard` (portrait card + candidate dropdowns + pass overlay), `LeadProfile` (hero + stats grid + copy-name icon), `ClassifyCard` (SalesNav link, email vetting, CRM status), `AppShell`, `ui.tsx` (Button/Card/CopyButton/…) |
| `frontend/src/app/(app)/` | `swipe` (deck + animations), `pipeline`, `dashboard`, `leaderboard`, `admin`, `analytics`, `new-incorps` (**stub**) |
| `data/uk_sic_codes.csv` | **The SIC reference data** (728 codes → description + our business grouping). Source of truth for `sic_lookup`; edit it and redeploy to change the table |
| `train_model.py` | Offline ML trainer (§8) |
| `experiment_sic.py` | SIC target-encoding experiment (§8/§9) |
| `backfill_screening_features.py` | One-time backfill of new screening_log columns (already run) |
| `Dockerfile.api`, `railway.api.json`, `DEPLOY.md` | API deployment |

## 3. Database changes since the Streamlit era

- **`sales_leads`**: added `directors_info` JSONB (per-director `{name, officer_id, appointments, url}`),
  `website_candidates` / `linkedin_candidates` JSONB (top-5 scored search results `{url,title,score}`).
  **Removed from the model: `is_nabd`, `contact_email`** — the DB columns still physically exist.
  They were kept only because the live Streamlit app selected them; **Streamlit is now retired, so
  they are safe to drop** (§10). Nothing in the React/API stack reads them.
- **`screening_log`** (the durable training features): added `sic_codes`, `incorporation_date`,
  `confidence_score`, `website_score`, `linkedin_score`. Historical rows were backfilled from
  sales_leads/pipeline_archive via `backfill_screening_features.py` (done; hard-cleared leads unrecoverable).
- **`ml_pipeline_analytics`** (the durable labels): added `website_candidates`, `linkedin_candidates`,
  `website_chosen`, `linkedin_chosen` — the learning-to-rank signal (candidate set + what the AE picked).
- **`sic_lookup`**: added `section` (**our** business grouping — "Software/Data", "Used Car Sales" —
  NOT the official SIC section letter). The old ~70-code hand-seeded dict in `sic_data.py` is gone;
  the table is now **replaced wholesale** from `data/uk_sic_codes.csv` (728 codes) by
  `load_sic_lookup()`, which runs on **API startup** (`api/main.py` lifespan) — so a deploy is all
  it takes to pick up an edited CSV. Manual path: `python sic_data.py`.
  **Loaded into Supabase 2026-07-16: 731 codes, 67 groups; 570/572 of the codes in use across the
  15,261 leads resolve (99.98% of leads).** Three gotchas baked into `sic_data.py`, don't undo them:
  1. **The CSV is zero-padded on read, lead data is NOT.** The CSV drops the leading zero on codes
     < 10000 (`1110` = SIC 2007 `01110`), so `read_sic_csv` pads it. But CH always sends 5-digit
     SIC 2007, so a 4-digit code *on a lead* is a retired **SIC 2003** code (`7414`, `7487` — 3
     leads, the only non-5-digit codes in the pool). Padding those would invent a code, and
     SIC 2003 `1110` (crude petroleum) would pad into `01110` (growing cereals) and render a
     confidently wrong description. Hence `parse_sic_codes` deliberately does not pad.
  2. **CH issues three codes outside the official list** — `74990` (non-trading), `99999` (dormant),
     `98000` (residents property mgmt) — so they live in `CH_EXTRA_CODES` and are merged in at load;
     a plain CSV replace loses them. Not rare: **74990 alone is on 111 leads**.
  3. The live table also has a stale **`category`** column (abandoned hand-grouping: 2 of 71 rows
     filled, one value literally `'Farming\r\n'`). Not in `models.py`, read by nothing —
     **safe to `ALTER TABLE sic_lookup DROP COLUMN category`**.
- All via the idempotent auto-migration blocks in `models.py` (`_ADDED_COLUMNS` + per-table ALTERs). **Adding a column = add to the Table + the migration block. And keep `pipeline_archive` in sync with `sales_leads`** — "Clear Pipeline" copies by column name and ERRORS if the archive is missing columns (this bit us once already).
- CRM statuses (classify dropdown): **"Won" is retired for GDPR**. Now: `Net New`,
  `Existing Lead - Unclaimed/Already Claimed`, `Existing Account - Unclaimed/Already Claimed`,
  `Disqualified`. A data fix for old rows may still be outstanding — check and run if needed:
  `UPDATE ml_pipeline_analytics SET crm_status='Existing Account - Already Claimed' WHERE crm_status='Won';`

## 4. The React swipe flow (differs from Streamlit)

Portrait Tinder-style card (hero = monogram + fit score + name + copy-icon; body = chips,
financials grid, then **Nature of business** — one line per SIC code, `01110 — Growing of cereals…`,
resolved server-side into `sic_detail` by `sic_data.with_sic_detail`). Next-2 cards preloaded behind, offset right + faded; approve plays a
tick + fly-to-My-Pipeline animation; advance is **optimistic** (API call in background, rollback on failure).
- **Sources are dropdowns** of the stored top-5 candidates (website shows domain, LinkedIn shows
  the `/company/` slug under a fixed prefix bar) + "None found" + "Other — type it" (LinkedIn Other =
  slug only, prefix fixed). Chosen URL → `corrected_*` if it differs from the scraped default.
- **Pass** = grey/blur overlay with 6 reason buttons, no source questions (`website_valid` etc. now
  null = "not asked" on passes — cleaner labels).
- **Approve** auto-triggers **director enrichment in the background** (officers + per-director
  appointment counts + officer-page link). The pipeline page's "Add to pipeline" button is the
  manual fallback / re-fetch. Needs `CH_API_KEY` on the API service.
- Classify card: **LinkedIn Search** (SalesNav company-keyword search), **Business Search**
  (copies bare domain to clipboard — user's work laptop blocks paste INTO the app, hence
  copy-buttons), one-at-a-time email vetting (most popular pattern first, ✓/✗, Mailmeteor link),
  CRM status, Save.

## 5. Pipeline / scoring — unchanged fundamentals

Staged pipeline (`pipeline.py`): Stage A cheap CH+HMRC → best-case gate; Stage B accounts parsing
→ realistic gate; Stage C DDG website+LinkedIn (now also stores top-5 candidates each). 5%
**holdout** bypasses gates (unbiased training data — do not break this). `lead_score` = rules in
`scoring.py` (noisy-OR, the ML seam); bar = admin slider (30–50 band, `settings.py`).
`rerun_pipeline.py` now resets **only `ready_for_swipe`** leads (not screened-out).
Admin "Gather & enrich" queues into `pipeline_jobs` for the Railway worker.

## 6. Analytics board (admin-only, `/analytics`)

Approval-by-industry-group (the `sic_lookup.section` rollup — coarser than per-code, so the
counts are big enough to read), approval-by-SIC top-20, feature↔approval correlations, CRM-status factor breakdown, score
calibration (does score predict approval), per-score-band factor table, enrichment coverage.
Reads the **current pool** (cleared leads drop out) — durable-log version is a known future improvement.

## 7. Admin dashboard (`/admin`)

Enrichment-strength slider (commits on release), Gather & enrich (job queue + live progress
polling + cancel), Lead distribute (default target **40**), Pipeline health metrics, AE
performance (remaining / assigned / approvals / SF entries), cleanup (clear working pool /
clear pipeline w/ confirm).

## 8. ML status (as of 2026-07-15)

- `train_model.py`: HistGradientBoosting + ratio features, holdout-separated eval, sigmoid
  calibration. Writes `lead_model.pkl` (gitignored). **475 labelled / 166 approvals**:
  MODEL ROC-AUC **0.705** / PR-AUC 0.587 vs RULES 0.588/0.439; holdout (n=26, rough) 0.642 vs
  0.509 — **model consistently beats the rules incl. on unbiased data**; calibration Brier 0.204, sane.
- **Not wired into the app yet.** Next ML step = a `score_lead` loader behind a flag
  (shared feature-engineering fn; model file → worker; sklearn+joblib deps; note the calibrated
  output tops out ~60 so the qualification bar's meaning shifts to approval-probability).
- SIC: target-encoding ≈ the native `sic_division` categorical (0.708 vs 0.705 — noise). **Not
  worth switching**; SIC is already in the model. But the SIC *signal itself* is real (§9).
- Tabular features are tapped out at this data size; the levers are (a) more swipes,
  (b) a web-judgment LLM agent (reads website/LinkedIn — a genuinely new signal).
- **Python 3.14 sklearn gotchas** (cost 3 debugging rounds): always pass a **shuffled
  StratifiedKFold object** to CalibratedClassifierCV/cross-val (default unshuffled int-cv folds
  can make a sparse feature all-empty → `sliding_window_view` crash); `cv="prefit"` no longer
  exists in this sklearn; drop features with <10 non-null values before fitting.

## 9. NEXT TASK — SIC-code screening (user request, 2026-07-15)

The user wants to **start screening out poor SIC codes — "maybe not completely"** — e.g.
pubs/restaurants and real estate.

> ### ⚠️ READ THIS BEFORE ACTING ON THE NUMBERS BELOW
> The original guidance here was built on a **475-lead sample where the damning codes had n=5–8**.
> The pool is now **942 decided / 481 approved (51% baseline)**, and the new
> `sic_lookup.section` grouping (§3) pools codes into samples of n=21–85. **Most of the "0%
> approval" findings did not survive.** Re-measured 2026-07-16:
>
> | | old claim (n=5–8) | now | verdict |
> |---|---|---|---|
> | Food service (`56101/56102/56302`) | 0% | **17%** group (11/65) | **real** — worst group, but not zero |
> | Construction trades (`43991/43999`) | 0% | `43999` **62%** (8/13); **Construction group 58%** (49/85) | **noise — screening this would have been a mistake** |
> | Real-estate letting (`68209`) | 0% | **27%** (3/11); Real estate group 39% (14/36) | weak-ish, small n |
> | Education (`85100`) | 0% | **0%** (0/9) | holds, but n=9 |
> | Software (`62012`) | 88% | **86%** (12/14); Software/Data group **84%** (36/43) | **holds** |
> | Real estate buy/sell (`68100`) | 60% | **30%** (3/10) | **reversed** |
>
> Lesson: at n≈10 a SIC code's rate is nearly meaningless. **Use the group view to decide *whether*
> a territory is weak, then the code view to decide *what* to screen** — and re-measure rather than
> trusting any number written here.

Design guidance for the implementer:
- **"Not completely" matters.** Prefer a **soft penalty in `scoring.py`** (e.g. a SIC-based
  strength/multiplier on the noisy-OR, or a subtraction) or a **config-driven SIC list**
  (app_settings or a table, editable from admin) feeding a Stage A gate — NOT a hardcoded drop.
  The table above is the argument for soft: food service at 17% still converts 1 in 6.
- **Never bypass the holdout.** Screened-by-SIC leads must still ride the 5% holdout unfiltered,
  or SIC screening biases exactly the training data that just proved SIC is predictive.
- Careful with granularity: 68100 and 68209 are both "Real estate activities" but sit at 30% vs
  27% — and `43999` (62%) vs `43991` (17%) split *inside* Construction. So **screen at 5-digit
  level, not by group and not by division**; the group is a lens for finding candidates, not the
  screening key.
- **`sic_lookup.section` (§3)** is the ready-made handle: 731 codes → 67 groups, and the analytics
  board's top chart already ranks approval per group with usable sample sizes.
- Log the reason (`screen_reason = "Stage A · SIC …"`) so the analytics board shows the effect.

## 10. Other open items

1. **Uncommitted at handover time** (check `git status` — it's the truth, this list rots):
   the ML scripts (`train_model.py`, `experiment_sic.py`, `backfill_screening_features.py`),
   SwipeCard LinkedIn-prefix tweak, enrichment candidate console-logging, `.gitignore` ML entries,
   the `DEPLOY.md` secret scrub, and the whole **SIC reference change** (`data/uk_sic_codes.csv`,
   `sic_data.py`, `models.py`, `api/*`, `LeadProfile.tsx`, analytics page, `tests/test_sic_data.py`).
   Commit+push deploys frontend (Vercel) + API (Railway) automatically — and the API deploy is what
   loads `sic_lookup` (§3).
2. **New Incorps page** — still a stub in the React app. The `ch_*` engine works, but its only UI
   was the Streamlit page, which is now retired — so this is the one feature that **lost its
   front-end at cutover**. Port `new_incorps_page.py` to React to get it back.
3. Old `ready_for_swipe` leads only get candidate dropdowns after `python rerun_pipeline.py` (local).
4. `score_lead` model loader behind a flag (§8).
5. **Streamlit cutover cleanup — now unblocked** (retired 2026-07-16), each independent:
   - Drop the dead `is_nabd` / `contact_email` columns (§3).
   - Delete the page files: `app.py`, `swipe_page.py`, `new_incorps_page.py` (port it first, see 2),
     `lead_card.py`, `admin_panel.py`, `ae_dashboard.py`, `ae_home.py`, `analytics_dashboard.py`,
     `auth.py`, `lead_score_distribution.py`, `.streamlit/`.
   - Single requirements set: only once the above are gone AND the **worker** is re-tested — it
     builds from the root `requirements.txt`, so dropping Streamlit there is what lets `.venv-api`
     and the venv split (§1) finally collapse into one environment.
   - Then the optional-Streamlit shims (`database.py`, `leaderboard.py`, `sic_data.py` try/except
     `import streamlit`) can become plain code.
6. Vercel preview deployments are login-gated (Deployment Protection) — AEs use the production URL.
7. Analytics on the durable logs instead of the live pool.

## 11. CH Lead Engine (unchanged)

Self-contained `ch_*` subsystem (new-incorps watcher, own tables/scoring/tests). See `ch_*` file
docstrings; operation: `ch_stream.py companies` (needs CH_STREAM_KEY) or `ch_backfill.py`, then
`ch_run_local.py`. The engine itself is unaffected by the Streamlit retirement (it's pure
Python + its own tables), but its **only UI was the Streamlit page**, so its "Send to swipe
pipeline" hand-off into `sales_leads` currently has no button — see §10.2.
CH_STREAM_KEY is now a **separate** Companies House application/key from the REST `CH_API_KEY`
(split 2026-07-16 so one leak can't burn both).

## 12. Command crib sheet

```bash
# API dev (repo root):
.venv-api\Scripts\python -m uvicorn api.main:app --reload --port 8000
# Frontend dev:
cd frontend && npm run dev            # build check: npm run build
# Pipeline (local, needs secrets):
python enrich_local.py [N|all]        # enrich sourced leads
python rerun_pipeline.py              # re-enrich ready_for_swipe leads only
python rescore_leads.py               # re-score from stored figures (fast)
python sic_data.py                    # reload sic_lookup from data/uk_sic_codes.csv
                                      # (also runs automatically on API startup)
# ML:
python train_model.py                 # train + evaluate vs rules, saves lead_model.pkl
python experiment_sic.py              # SIC feature experiment
```
