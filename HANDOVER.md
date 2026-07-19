# Matchmaker 2.0 — Handover

A lead-triage platform. It sources UK companies from Companies House, runs them through a
**staged enrichment pipeline** (cheap checks first, expensive last), **scores each for sales
fit**, and lets AEs swipe qualified leads Tinder-style — Pass or Approve — then classify
approved leads into CRM statuses. Points/leaderboard + admin control centre + analytics.

> **Read this first.** The stack is **Next.js on Vercel + FastAPI on Railway**, off branch
> `react-rebuild` (the GitHub **default** branch). All work happens here.
>
> **Streamlit is GONE (2026-07-17): app retired 07-16, then all page files deleted, every
> `st.*` code path removed, and streamlit dropped from the root `requirements.txt`.** The whole
> import chain is verified to work with streamlit uninstalled. That also ended the Starlette
> 1.x/0.41 conflict — **one venv can now run the API, the worker, and every script** (§1).
>
> **The immediate next task is SIC-code screening — see §9 before anything else.**

---

## 1. Infra & deployment (react-rebuild stack)

| Piece | Where | Notes |
|---|---|---|
| **Frontend** (`frontend/`) | **Vercel**, builds `react-rebuild` | Project setting **Root Directory = `frontend`**. Env var `NEXT_PUBLIC_API_URL` = the Railway API URL (must include `https://` — without a scheme the browser treats it as a relative path; `api.ts` now normalises this). |
| **API** (`api/`) | **Railway** service "MatchMaker2" — `matchmaker2-production.up.railway.app` | **Docker** build via `Dockerfile.api` + `railway.api.json` (set in service Settings → Config-as-code). Deliberately NOT named `Dockerfile`/`railway.json` — a default-named file would also be applied to the worker and break it. Env vars: `DB_PASSWORD`, `SUPABASE_HOST/PORT/USER/DBNAME`, **`CH_API_KEY`** (without it director enrichment silently returns "No directors found"), `JWT_SECRET`, `CORS_ORIGINS` (exact Vercel origin, no trailing slash), `PORT=8000`. Health: `/health`; docs: `/docs`. |
| **Worker** (`lead_worker.py`) | **Railway** service "worker" | Nixpacks + root `requirements.txt` + `Procfile` (`worker: python lead_worker.py`). Drains `pipeline_jobs` (the admin "Gather & enrich" button) **and self-enqueues on a schedule** — see the auto-scheduler in §7. |
| ~~**Legacy Streamlit**~~ | — | **Retired 2026-07-16.** No longer deployed. Page files still in the tree pending cleanup (§10). |
| **DB** | Supabase Postgres, Session pooler + `pg8000` | Connection in `database.py`; credentials from the environment via `env_loader.py` (root `.env` locally, Railway Variables headless). |
| **CHStream** | separate repo `Jaceur/CHStream`, own Railway service | Not part of this repo anymore. |

**Local dev:** the historical two-venv split is **no longer required** — removing Streamlit
(2026-07-17) removed the Starlette 1.x/0.41 conflict that forced it. One venv with the root
`requirements.txt` + `api/requirements.txt` installed runs everything. The `.venv-api` /
`.venv-ml` venvs still exist locally and still work (nothing forces a rebuild); API from the repo
root: `.venv-api\Scripts\python -m uvicorn api.main:app --reload --port 8000` (or any venv with
fastapi installed). Frontend: `cd frontend && npm run dev`. The api/requirements pin set is still
what the Docker image installs — keep it lean, it's why the API image doesn't ship pandas' friends
like ddgs/tesseract.

**Secrets: ONE gitignored `.env` at the project root** (copy `.env.example`), loaded by `env_loader.py` and shared by the API, the worker and every local script. Consolidated 2026-07-16 — it used to be split (`api/.env` for the API, `.streamlit/secrets.toml` via `st.secrets` for everything else), which meant a rotated DB password had to be pasted twice; it wasn't, so every local script broke while the deployed app kept working. **`.env` is not TOML — paste values raw, no quotes** (the live DB password contains a `"`, which is what silently broke the old TOML file). Full walkthroughs in `api/README.md` and `DEPLOY.md`.

## 2. Architecture (file map)

Shared Python modules at repo root (imported by the API **and** the worker — `database`, `models`,
`settings`, `scoring`, `sic_weights`, `pipeline`, `enrichment`, `second_enrichment`, `leads`,
`directors`, `sourcing`, `leaderboard`, `sic_data`, `ml_data`, `env_loader` + the `ch_*` engine).
No module imports Streamlit anymore — caching is plain `functools.lru_cache`, secrets are
`env_loader.py` (imported by `database.py` for the DB and `ch_client.py` for the CH keys, which
between them cover every entry point).

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
| `env_loader.py` | Loads the root `.env` into `os.environ`. The ONE place secrets come from |
| `ml_data.py` | **The labelled-lead dataset query** (screening_log ⋈ ml_pipeline_analytics). Every training-data consumer (trainer, SIC weights, future models) reads through it — it owns the durable-log-not-live-pool rule |
| `sic_weights.py` | SIC industry multiplier on lead_score (§5) |
| `train_model.py` | Offline ML trainer (§8) |
| `experiment_sic.py` | SIC target-encoding experiment (§8/§9) |
| `backfill_screening_features.py` | One-time backfill of new screening_log columns (already run) |
| `Dockerfile.api`, `railway.api.json`, `DEPLOY.md` | API deployment |

## 3. Database changes since the Streamlit era

- **`sales_leads`**: added `directors_info` JSONB (per-director `{name, officer_id, appointments, url}`),
  `website_candidates` / `linkedin_candidates` JSONB (top-5 scored search results `{url,title,score}`).
  **`is_nabd` / `contact_email` — DROPPED 2026-07-18** (`drop_dead_columns.py`, run against both
  sales_leads and pipeline_archive). They'd been out of the ORM for ages and only survived
  physically because the retired Streamlit app SELECTed them; nothing in the current stack did.
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
- **A HOLDOUT lead's fit score shows `??`, not a number** (`LeadProfile`, keyed off `is_holdout`).
  Deliberate: holdouts exist to measure the filter honestly, and showing an AE a low score would
  bias the very verdict being sampled. Don't "fix" this by revealing the score. (Residual risk: `??`
  only ever appears on holdouts, so an AE could in principle learn to spot them — if that shows up
  in the data, hide the score for everyone rather than un-hiding it here.)
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

## 5. Pipeline / scoring

Staged pipeline (`pipeline.py`): Stage A cheap CH+HMRC → best-case gate; Stage B accounts parsing
→ realistic gate; Stage C DDG website+LinkedIn (now also stores top-5 candidates each).
`lead_score` = rules in `scoring.py` (noisy-OR, the ML seam) **× the SIC multiplier** (below);
bar = admin slider (30–50 band, `settings.py`).
`rerun_pipeline.py` now resets **only `ready_for_swipe`** leads (not screened-out).
Admin "Gather & enrich" queues into `pipeline_jobs` for the Railway worker.

**Tunables without a redeploy** (added 2026-07-17): the knobs ship as code defaults but read
`app_settings` overrides via `settings.get_int_setting`/`get_float_setting` — insert a row to
override, delete it to fall back. Keys: `holdout_rate` (default 0.05), `pending_target` (20),
`sic_shrink_k` (25), `sic_min_group_n` (15), `sic_mult_min`/`sic_mult_max` (0.5/1.5),
`sic_min_total` (100), and the auto-scheduler's `auto_source_enabled` (1), `auto_source_interval_hours`
(4), `auto_source_batch` (500), `auto_source_max_awaiting` (1000) — §7. No admin UI yet — a psql one-liner does it:
`INSERT INTO app_settings(key,value) VALUES ('holdout_rate','0.03') ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value;`
Actual funnel (17.6k leads): **12% binned at Stage A** (dormant, avg score 0), **71% at Stage B**,
**18% qualify**. So Stage A is in practice a dormancy filter and Stage B is the real gate.

### SIC industry weighting (`sic_weights.py`, added 2026-07-17)
Industries convert wildly differently, and the rules can't see it. Each group's observed approval
rate becomes a **multiplier on the finished score** (a multiplier, not a noisy-OR strength — the
noisy-OR only ever pushes UP, so it cannot express "this industry is bad"; and proportional scaling
is what keeps it "screen, but not completely"). Damped by sample size:

- Below **`MIN_GROUP_N`=15** decided leads → multiplier is exactly **1.0**. Shrinkage alone wasn't
  enough of a guard: 100% approval from 4 leads still earned a 1.27× boost.
- Above it: `w = n/(n+25)`; `effective = w·rate + (1−w)·baseline`; `multiplier = effective/baseline`,
  clamped to **0.5–1.5**.
- The multiplier is driven by the **distance from baseline**, so a group sitting at the baseline
  (Construction: 33% vs 34%) lands at 1.0 whatever its n. The weight only damps noise.
- Recomputed **every pipeline run** from the log, so it sharpens by itself as leads get swiped.
  Stored per lead in `sales_leads.sic_multiplier` + `screening_log.sic_multiplier` so any score is
  auditable, and Stage B's `screen_reason` names the penalty when it's what tipped a lead out.
- Rates come from the **durable log**, NOT `sales_leads` — see the warning in §6.
- Live at 2026-07-17 (baseline 34%, n=541): Restaurants/Pubs **0.50**, Financial 0.76, Human health
  0.80, Construction 0.98 (no change), Retail 1.19, Manufacturing 1.26, Software/Data **1.50**.
  Only 11 of 55 groups clear n=15; the rest are untouched.

### The 5% holdout — **it was broken, don't re-break it**
A random 5% bypass the gates so we learn what the filter would have binned. The pipeline always did
this correctly; **the distribution layer threw them away**. `top_up_allocation` and
`get_pending_leads` both filtered `lead_score >= bar`, so 170 of 171 below-bar holdout leads sat
unswiped forever — i.e. the "unbiased" holdout was ~35 above-bar leads and 1 below-bar one, and the
model's "honest test" in §8 was measuring nothing of the sort. Fixed 2026-07-17 via `leads._eligible`
/ `leads._sort_score`:
- Both queries let a holdout past the bar.
- A holdout sorts at a **random rank within the eligible band** — not its real score (it would sink
  to the bottom, and the draft stops once reps are full, so it'd never be handed out) and not a
  fixed rank like the bar (every eligible lead is ≥ bar, so that IS the bottom, and a predictable
  position leaks the score the card hides).
- `admin.py`'s `awaiting_allocation` mirrors the same exemption or the dashboard under-reports.

**This is load-bearing for the SIC weighting**: penalise a group and its leads stop clearing the
bar, so without the holdout its sample freezes and a wrong penalty becomes permanent and
self-fulfilling. The two features only work together.

## 6. Analytics board (admin-only, `/analytics`)

Approval-by-industry-group (the `sic_lookup.section` rollup — coarser than per-code, so the
counts are big enough to read), approval-by-SIC top-20, feature↔approval correlations, CRM-status factor breakdown, score
calibration (does score predict approval), per-score-band factor table, enrichment coverage.

**Reads the DURABLE log now (fixed 2026-07-18).** `_decided_frame` in `api/analytics.py` pulls
`screening_log ⋈ ml_pipeline_analytics` via `ml_data.load_labelled_leads`, so approval rates are
real and immune to "Clear working pool". `_coverage()` is the deliberate exception — it's a
live-pool question ("what share of my CURRENT enriched leads have each field").

> **Why it mattered** (kept as the cautionary tale): it used to read the live pool
> (`sales_leads WHERE status IN ('approved','archived')`), but `leads.clear_database()` deletes
> every lead `is_distinct_from('approved')` — wiping PASSES, keeping APPROVALS — so every clear
> ratcheted the measured rate up invisibly. Measured at the time: baseline **51% live vs 34%
> durable**; Restaurants/Pubs 17% vs 6%; Construction 58% vs 33%. Group *ordering* survived but the
> magnitudes didn't. `clear_database()` deleting passes at all is arguably still a bug (it destroys
> the negative half of the label set) — see §10.
> **Lesson to keep:** anything that reads approval rates goes through `ml_data`, never `sales_leads`.

## 7. Admin dashboard (`/admin`)

Enrichment-strength slider (commits on release), Gather & enrich (job queue + live progress
polling + cancel), Lead distribute (default target **40**), Pipeline health metrics, AE
performance (remaining / assigned / approvals / SF entries), the shadow-model scoreboard (§8),
cleanup (clear working pool / clear pipeline w/ confirm).

### Auto-scheduler (worker, `lead_worker.maybe_auto_source`, added 2026-07-18)
When idle, the worker checks ~every 5 min and **enqueues a 500-lead source+enrich job roughly every
4 hours — but only while the unassigned-qualified buffer is under 1000.** Over the cap it holds off
without resetting the clock, so it resumes the instant the buffer drains (that's the "off until it
dips below 1000 again" behaviour). Auto-jobs are ordinary `pipeline_jobs` rows
(`requested_by='auto-scheduler'`), so they appear in the panel like manual ones; the cadence clock
(`last_auto_source_at`) lives in `app_settings` so a redeploy doesn't reset it. It won't stack on a
manual job (skips while any job is pending/running). The decision is a pure function
(`_auto_source_decision`, unit-tested). **Live-tunable via `app_settings`** (psql; no UI yet):
`auto_source_enabled` (default 1 — set **0 to switch it off**), `auto_source_interval_hours` (4),
`auto_source_batch` (500), `auto_source_max_awaiting` (1000). "Awaiting assignment" = the same
`ready_for_swipe`, unassigned, qualified-or-holdout pool `top_up_allocation` draws from.

## 8. ML status (as of 2026-07-18)

- **SHADOW MODE IS LIVE (2026-07-18).** The trained model scores every Stage-C lead alongside the
  rules and stores `model_score`, but **drives nothing** — no gate, no ordering. It's here to gather
  model-vs-rules evidence on live leads before trusting it. Pieces:
  - `ml_features.py` — feature engineering SHARED by train + serve (so they can't drift). Verified
    byte-identical: row-at-a-time serving == batch training predict, 0 mismatches.
  - `model_scorer.py` — `score_lead_model(lead) → 0-100 or None`. FAIL-SAFE: missing model / odd
    lead / pre-Stage-C lead all return None, never raise (shadow scoring must not break enrichment).
  - `pipeline.py` writes `model_score` at Stage C; `rescore_leads.py` + `backfill_model_scores.py`
    populate existing rows. Column on sales_leads / pipeline_archive / screening_log.
  - **Admin panel → "🧪 Shadow model"**: coverage %, model-vs-rules AUC, and **precision@top-20%**
    (the deployment question: would a model-ranked queue put better leads on top?). Reads the durable
    log; AUC computed in pure Python (`admin._auc`, matches sklearn) so the API image needs no
    sklearn. Shows a "gathering data" state until ≥30 decided-and-scored leads exist.
  - **Model delivery decision made: `lead_model.pkl` is now COMMITTED** (~800KB, un-gitignored). The
    Railway worker builds from git and needs it; kept OUT of the API image (`.dockerignore`) since
    the API only reads the column. sklearn+joblib added to the **root** requirements (worker), not
    `api/requirements.txt`. Retrain = `train_model.py`, commit the new `.pkl`, push.
- `train_model.py`: HistGradientBoosting + ratio features, holdout-separated eval, sigmoid
  calibration. **1134 labelled / 547 approvals (48%)** after the approval-leak fix (§8 below):
  MODEL ROC-AUC **0.745** / PR-AUC 0.725 (CV) vs RULES 0.621/0.611; holdout n=126 MODEL 0.771 vs
  RULES 0.786 (⚠️ range-restricted — see below); Brier 0.205, top band now honest (n=67, 69%→69%).
- **Not wired into the app yet, and a straight swap of `score_lead` would be wrong.** Investigated
  2026-07-17 — three blockers, in order of severity:
  1. **Train/serve skew.** All 736 labelled rows have `website_score` (100%) — only leads surviving
     to Stage C ever reach an AE to be labelled. But `score_lead()` runs at Stage **A and B**,
     before Stage C exists. A model in the gate would score with three features permanently blank
     that it always had in training. **The model can only score AFTER Stage C** — which is where
     ranking happens anyway (see below).
  2. **The scales aren't comparable.** On the same leads: rules median **60** (98% clear the bar of
     37); calibrated model median **31** (38% clear it). The model centres on the base rate because
     it's calibrated — correct, but it means a swap makes the 30–50 bar silently ~3× stricter and
     collapses the AE queues. The bar would need re-deriving from scratch.
  3. `lead_model.pkl` is **gitignored** (670KB) and Railway builds from git, so the worker gets no
     model. Needs a delivery decision. sklearn+joblib belong in the **root** requirements only —
     nothing under `api/` calls `score_lead`; only the worker and local scripts score.
  Also: the `LeadFeatures` seam is too narrow to be the drop-in it advertises (10 fields; the model
  needs 23 — SIC, age, web scores, ratios). A model scorer needs the row, not `LeadFeatures`.
- **The recommended shape: hybrid, split by JOB, not by averaging the scores.** Rules keep the GATE
  (cheap, run where the model has no features, and they bin 82% of 17.6k — real money). The model
  takes the RANKING, scored post-Stage-C where its features exist: `get_pending_leads` orders by
  `lead_score`. Phase it: **✅ `model_score` in shadow mode (done 2026-07-18)** → order the queue by
  it behind a flag → measure via the admin scoreboard → only then consider the gate, and that needs
  a *separate* model trained on Stage-B-available features.
- ⚠️ **Don't read the holdout AUC as "model ≈ rules".** The holdout is ~65% below-bar shells
  (score ~5, ~1% approve) + a thin above-bar slice — ranking that mixture is trivial and the rules
  ARE the score defining the two clumps, so ~0.78 for both is near-circular. The deployment question
  (rank the QUALIFIED pool better) is what the shadow scoreboard's precision@top measures on live
  swipes; the holdout can't answer it. The earlier "rules are a coin flip (0.531)" claim was on the
  broken 28-lead holdout — **retracted**.
- **Retrained 2026-07-17** (n=551, 34% approval): MODEL ROC-AUC 0.694 / PR-AUC 0.546 vs RULES
  0.600/0.446. Holdout n=28: MODEL **0.708** vs RULES **0.531**. Brier 0.194, bands honest
  (says 29% → 22% actual; says 63% → 67%). ⚠️ That holdout number is **not trustworthy** — see §5:
  the holdout was broken, so those 28 are ~27 above-bar leads. Re-measure once the fix has fed
  through real below-bar swipes.
- **The trainer sees a different population from the analytics board**: 551 labelled at 34% vs 922
  decided at 51%. ~390 decided leads have no `ml_pipeline_analytics` row, and the board's rate is
  inflated by `clear_database()` (§6). Worth understanding before trusting either.
- SIC: target-encoding ≈ the native `sic_division` categorical (0.708 vs 0.705 — noise). **Not
  worth switching**; SIC is already in the model. But the SIC *signal itself* is real (§9).
- Tabular features are tapped out at this data size; the levers are (a) more swipes,
  (b) a web-judgment LLM agent (reads website/LinkedIn — a genuinely new signal).
- **Training-data capture widened 2026-07-17** (all flowing automatically from the next deploy):
  - `ml_pipeline_analytics` now snapshots **decision context at the swipe**: `lead_score` (the
    score the queue ranked by — screening_log's copy goes stale after a rescore), `sic_multiplier`,
    `is_holdout` (select an unbiased eval slice with no join), and `hours_in_queue`
    (assigned → decided; a stale-pile/reluctance signal). Written by `leads.build_ml_row`.
  - **Approve-side dwell time**: the pass path always logged `dwell_time_seconds`; approves wrote
    NULL — the more interesting half of the decision-latency signal was missing. The swipe card now
    sends it on approve too.
  - **Labels are written AT APPROVE, not at classify (2026-07-18).** The old flow only wrote the
    ML row at classify, so an approve the AE never classified never became training data — that
    silently leaked **271 approvals against a training set holding 275**. Now `approve_lead` inserts
    the label row (`is_worth_it=TRUE`) at swipe time and `classify_lead` UPDATEs it with the CRM
    outcome (INSERT fallback for pre-change approves). ⚠️ Consequently, **"unclassified" now means
    `crm_status IS NULL`, not "no ML row"** — the pipeline-page queries, `me.py`'s into-CRM stat
    (and anything new) must key off `crm_status`, never row-existence.
    One-time recovery of the 271 leaked approvals: `python backfill_approval_labels.py` — **run it
    AFTER this change is deployed**, or the new rows hide live leads from the classify list.
  - All label consumers read through **`ml_data.load_labelled_leads()`** — one owner for the
    join and for the durable-log-not-live-pool rule (§6).
- **Python 3.14 sklearn gotchas** (cost 3 debugging rounds): always pass a **shuffled
  StratifiedKFold object** to CalibratedClassifierCV/cross-val (default unshuffled int-cv folds
  can make a sparse feature all-empty → `sliding_window_view` crash); `cv="prefit"` no longer
  exists in this sklearn; drop features with <10 non-null values before fitting.

## 9. SIC-code screening — **DONE 2026-07-17** (was the "next task")

Shipped as the industry multiplier in `sic_weights.py` — mechanics in §5, don't duplicate them here.
The user's brief was "screen out poor SIC codes, **maybe not completely**", weighted by sample size.
The history of how the numbers moved is kept because it's the cautionary tale:

> The original guidance was built on a **475-lead sample where the damning codes had n=5–8**, read
> off the analytics board — which we now know **inflates approval rates** (§6). Almost none of it
> survived contact with more data and an honest source:
>
> | | original claim (n=5–8) | honest, 2026-07-17 | verdict |
> |---|---|---|---|
> | Food service | 0% | Restaurants/Pubs group **6%** (n=51) | **real** — the one true signal, now ×0.50 |
> | Construction trades | 0% | Construction group **33%** vs 34% baseline | **noise — screening it would have been a mistake** |
> | Real-estate letting | 0% | Real estate group 30% (n=20) | ~neutral (×0.95) |
> | Education `85100` | 0% | Education group 25% (n=16) | mild (×0.90) |
> | Software `62012` | 88% | Software/Data **81%** (n=21) | **holds** — ×1.50 |
>
> Two lessons, both now encoded in the design rather than in prose: **at n≈10 a rate means nothing**
> (hence `MIN_GROUP_N`), and **the baseline you compare against decides the sign of your answer**
> (law at 30% looks damning against the board's inflated 51% and is a non-event against the true
> 34%). Re-measure with `python sic_weights.py`; never trust a number written in this file.

Still open / deliberately not done:
- **Per-5-digit-code screening.** Weighting is at GROUP level. Codes still split inside a group
  (`43999` vs `43991`), so a group is a blunt instrument — but no single code has the sample to
  justify its own multiplier yet. Revisit when codes reach n≈15 individually.
- **No admin control.** `SHRINK_K` / `MIN_GROUP_N` / the 0.5–1.5 clamp are constants. If tuning
  becomes routine, move them to `app_settings` behind the admin page.
- **Existing leads keep their old scores** until `python rescore_leads.py` runs (§12).

## 10. Other open items

1. **Uncommitted at handover time** (check `git status` — it's the truth, this list rots):
   the ML scripts (`train_model.py`, `experiment_sic.py`, `backfill_screening_features.py`),
   SwipeCard LinkedIn-prefix tweak, enrichment candidate console-logging, `.gitignore` ML entries,
   the `DEPLOY.md` secret scrub, and the whole **SIC reference change** (`data/uk_sic_codes.csv`,
   `sic_data.py`, `models.py`, `api/*`, `LeadProfile.tsx`, analytics page, `tests/test_sic_data.py`).
   Commit+push deploys frontend (Vercel) + API (Railway) automatically — and the API deploy is what
   loads `sic_lookup` (§3).
2. **New Incorps page** — still a stub in the React app. The `ch_*` engine works, but its only UI
   (the Streamlit page) is retired and now **deleted** — recover `new_incorps_page.py` from git
   history as the reference when porting it to React. The one feature with no front-end.
3. Old `ready_for_swipe` leads only get candidate dropdowns after `python rerun_pipeline.py` (local).
4. `score_lead` model loader behind a flag (§8).
5. **Streamlit cutover cleanup — DONE.** Secrets → `.env` (07-16); page files, `st.*` paths,
   devcontainer, `matchmaker2.zip`, streamlit pins (07-17); dead `is_nabd`/`contact_email` columns
   dropped (07-18, §3). Nothing left.
6. Vercel preview deployments are login-gated (Deployment Protection) — AEs use the production URL.
7. **Analytics on the durable log — DONE 2026-07-18** (§6). The board no longer inflates.
8. **The holdout backlog.** The §5 fix makes 165 stranded holdout leads allocatable at once —
   ~71% of the current unassigned pool, so the next distribute would hand AEs mostly `??` leads.
   They're below-bar by design, so approvals (and the leaderboard) would dip while it drains.
   Consider capping the holdout share per draft if that bites; steady state is ~20% of a pile.
9. `clear_database()` deleting passes is arguably a bug in its own right, not just an analytics
   problem — it destroys the negative half of every training label set. Consider archiving passes
   the way `clear_pipeline()` archives approvals.

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
python rescore_leads.py               # re-score from stored figures (fast). NOTE: now also
                                      # applies the SIC multiplier, so it will move ~15k scores
python sic_data.py                    # reload sic_lookup from data/uk_sic_codes.csv
                                      # (also runs automatically on API startup)
python sic_weights.py                 # print the live industry multipliers + the data behind them
python backfill_approval_labels.py    # one-time: recover the 271 pre-2026-07-18 leaked approve labels
                                      # (run AFTER deploying the label-at-approve change)
python train_model.py                 # retrain; commit the new lead_model.pkl to ship it to the worker
python backfill_model_scores.py       # one-time: shadow-score existing leads so the admin panel
                                      # lights up now (needs sklearn — run in .venv-ml)
# ML:
python train_model.py                 # train + evaluate vs rules, saves lead_model.pkl
python experiment_sic.py              # SIC feature experiment
```
