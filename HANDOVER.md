# Matchmaker 2.0 — Handover

A Streamlit lead-triage app. It sources UK companies from Companies House, runs them through a
**staged enrichment pipeline** (cheap checks first, expensive ones last), **scores each for sales
fit**, and lets Account Executives (AEs) "swipe" through qualified leads Tinder-style — Pass or
Approve — then classify approved leads into Salesforce CRM statuses. Includes a points/leaderboard
system and an admin control centre.

> **Read this first if you're picking up the project.** The big change in the most recent work was a
> ground-up redesign of how leads are enriched and scored (sections 4–6). The scoring model was the
> last thing being tuned and has **one uncommitted change awaiting sign-off** — see section 9.

---

## 1. Infra & deployment

- **Host:** Streamlit Cloud, deploys from GitHub **`Jaceur/MatchMaker2`** (branch `main`).
- **Entry point:** `app.py` (Streamlit Cloud → Settings → Main file path must be `app.py`).
- **DB:** **Supabase** (managed Postgres). Reached over the **Session pooler** (IPv4-friendly; the
  direct `db.<ref>.supabase.co` host is IPv6-only and unreachable from Streamlit Cloud) using the
  pure-Python `pg8000` driver + TLS. All connection logic is in `database.py`. (Migrated off Google
  Cloud SQL 2026-07-06.)
- **Secrets** (`.streamlit/secrets.toml`, gitignored, present locally): a `[supabase]` table
  (`host`, `port`, `user`, `dbname`), plus top-level `DB_PASSWORD` (the Supabase DB password) and
  `CH_API_KEY`. The auth cookie is signed with an HMAC key derived from `DB_PASSWORD`, so changing
  the DB password logs everyone out once (harmless — they just log back in).
- **Python 3.14** on the deploy env (bleeding edge — watch for missing wheels if adding deps).
- **Run locally:** `streamlit run app.py` (needs the secrets file + `pip install -r requirements.txt`).
- **Git:** commits are made locally on `main`; **the user pushes** (don't push). At the time of this
  handover `main` is level with `origin/main` apart from the uncommitted files in section 9.

## 2. Architecture (file map)

Import DAG is acyclic: `database → models → settings → {auth, leads, sourcing, enrichment, directors,
second_enrichment, scoring, sic_data} → pipeline → {swipe_page, ae_dashboard, ae_home, admin_panel,
leaderboard, lead_card} → app`.

| File | Responsibility |
|---|---|
| `app.py` | Entry: page config, password migration + SIC seed (once per process), cookie session, **routing** |
| `database.py` | The one `engine` + `metadata` (Cloud SQL connector), imported everywhere |
| `models.py` | **All table schemas** + `create_all` + auto-migration block (`_ADDED_COLUMNS`) |
| `settings.py` | **Runtime settings stored in the DB** (`app_settings` table). The qualification-bar slider lives here: `get_qualify_bar()` (30–70), `get/set_qualify_percent()` (0–100%) |
| `scoring.py` | **`score_lead(LeadFeatures) → 0-100` sales-fit score — the ML swap point** (section 6). Also `best_possible_score`, `features_from_mapping`, `account_tier` |
| `pipeline.py` | **The staged screening pipeline** — `screen_lead` (Stage A/B/C + holdout), `run_pipeline`. Replaces the old first-then-second enrichment split (section 4) |
| `enrichment.py` | **Building blocks** used by the pipeline: `clean_company_name`, `fetch_ch_signals` (account_type, director change), `fetch_trade_activity` (HMRC import/export), `fetch_web_presence` (DDG website + LinkedIn), `score_website_match`, `score_linkedin_match`. DDG calls are paced/retried (`_ddg_text`) |
| `second_enrichment.py` | Parses filed **accounts documents** (iXBRL + PDF/OCR) → `second_enrich_lead(crn)` returns employee count + financials. Called as the pipeline's Stage B |
| `leads.py` | `get_pending_leads`, `top_up_allocation` (both **gate on `lead_score ≥ get_qualify_bar()`**, order by `lead_score DESC, confidence_score DESC`), `build_ml_row`, `engineer_ml_features`, clear DB / clear pipeline, `award_activity` |
| `sourcing.py` | Companies House advanced-search → new `sourced` leads |
| `directors.py` | CH officers API (3 youngest directors → "First Last"), email-format candidates, `domain_from_url`. `enrich_lead_directors` **preserves `updated_at`** so enriching doesn't bump a My-Pipeline lead to the top |
| `swipe_page.py` | `main_app` — the swipe card; validity toggles; `pass_control`; correction inputs |
| `ae_dashboard.py` | **My Pipeline** (`render_ae_pipeline`): classify cards, `classify_lead` |
| `ae_home.py` | **AE Dashboard** (personal: pipeline count, into-Salesforce, points, change password) |
| `admin_panel.py` | **Admin Control Center**: cloud source+enrich job queue, clear buttons, **qualification-bar slider**, team top-up allocation, pipeline-health metrics (`screened_out` / `qualified`) |
| `analytics_dashboard.py` | **Analytics** page (admin-only, read-only, `render_analytics`): ML-readiness gauge (labelled leads needed before training `score_lead` — counts `screening_log` ⋈ `ml_pipeline_analytics`), lead funnel, screen-out reasons, score-vs-approval calibration, enrichment coverage |
| `leaderboard.py` | AE leaderboard + `compute_points` |
| `lead_card.py` | **Shared `render_profile`** (the Tinder/Revolut card); `_size_chip` uses `scoring.account_tier` |
| `sic_data.py` | SIC code reference dict + loader + cached `get_sic_lookup` |
| `rescore_leads.py` | **Re-compute `lead_score`** for existing leads from already-stored figures (fast, no internet) — run after a scoring change |
| `rerun_pipeline.py` | **Full re-run**: reset every non-swiped lead to `sourced` and push it back through the whole pipeline (slow, re-fetches everything) |
| `enrich_local.py` (+ `.bat`) | **Local** pipeline runner (faster, no cloud timeout; Stage B's OCR is local-only) |
| `test_accounts.py` | **Diagnostic — keep.** Dumps one company's accounts iXBRL tags for debugging the parser |

## 3. Database tables

- **`sales_leads`** — the working lead pool. Key columns: `crn`, `company_name`, `incorporation_date`,
  `sic_codes`, `website_url`/`linkedin_url` (+ `corrected_*`, `*_accurate`), **`status`**
  (`sourced` → `screened_out` *or* `ready_for_swipe` → `approved`/`archived`), `assigned_ae_username`,
  **`lead_score`** (sales fit, the allocation gate), **`confidence_score`** (now **only** website/LinkedIn
  data confidence — a tiebreaker, no longer the gate), `website_score`/`linkedin_score`, `account_type`,
  `director_change_recent`/`last_director_change`, `import_activity`/`export_activity`,
  `active_directors`/`directors_enriched`, `employee_count` + financials (`turnover`, `cash_at_bank`,
  `foreign_exchange`, `trade_debtors`, `trade_creditors`, `admin_expenses`, `bank_loans_overdrafts`),
  `second_enriched`, **`screen_reason`** (why a lead was screened out / kept as a holdout),
  **`is_holdout`**, `is_nabd` (**means "Won"**), `rejection_reason`.
- **`screening_log`** — **the ML training logbook.** One row written **per lead per pipeline run** with
  the features seen, the `lead_score`, the `qualify_bar` at the time, whether it `qualified`, whether it
  was a holdout, and the `screen_reason`. This is the unbiased dataset a future model trains on.
- **`app_settings`** — key/value runtime settings (currently `qualify_percent`, the slider value).
- **`ml_pipeline_analytics`** — older ML log; one row per Pass/classify (from `leads.build_ml_row`).
- **`users`** — login (`username` lowercase, `password` bcrypt, `role`: `admin`/`ae`).
- **`pipeline_archive`** — snapshot of approved leads taken on "Clear Pipeline".
- **`director_emails`** — one row per (director × email-format guess) + the AE's verdict.
- **`ae_stats`** — per-AE counters → leaderboard points.
- **`sic_lookup`** — SIC code → description reference (~50 common codes seeded).

**Migrations:** `models.py` runs `create_all` (creates missing **tables**) + idempotent
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for everything in `_ADDED_COLUMNS` (lead_score,
employee_count, the financial BIGINTs, `screen_reason`, `is_holdout`, the screening_log columns, etc.).
**Adding a column = add it to `models.py` + `_ADDED_COLUMNS`; no manual SQL.** A fresh DB gets
everything from `create_all`.

## 4. Lead lifecycle (the staged pipeline)

The old "first enrichment → tier → second enrichment" split was **replaced by one staged pipeline**
(`pipeline.py`) that does cheap things first and bins a lead the moment it can't possibly qualify, so
the slow/expensive steps only ever run on survivors.

1. **Source** (admin) → `sales_leads` `status='sourced'`.
2. **Pipeline** (admin "Run Pipeline" button, or `enrich_local.py` / `rerun_pipeline.py` locally),
   per lead, in `screen_lead`:
   - **Stage A — cheap:** Companies House `account_type` + recent-director-change, HMRC import/export.
     Compute a provisional `lead_score`. **"Start safe" gate:** bin (`status='screened_out'`) **only if
     even the lead's `best_possible_score`** (assuming every not-yet-seen figure turns out great) **is
     below the bar** — so a good lead is never dropped early just because its strong signals come later.
   - **Stage B — costly:** parse the filed accounts (`second_enrich_lead`) → employees, turnover, cash,
     FX, debtors/creditors. Re-score on the now-real figures; bin if `lead_score < bar`.
   - **Stage C — costly:** DDG website + LinkedIn search → `confidence_score`. Set
     `status='ready_for_swipe'`.
   - **Holdout:** a random `HOLDOUT_RATE = 5%` of leads **skip the gates** and go through whatever they
     score (flagged `is_holdout`, with a `screen_reason`). This keeps the training data unbiased — we get
     outcomes for leads the filter would otherwise have binned.
   - Every lead (qualified, screened-out, or holdout) writes a `screening_log` row.
3. **The qualification bar** is admin-tunable (section 5). A lead qualifies when `lead_score ≥ bar`.
4. **Allocate** (admin) → assigns qualified, unassigned leads to an AE (highest `lead_score` first).
5. **Swipe** (AE) → validate website/LinkedIn, then **Pass** (reason → archive + ML log) or **Approve**.
6. **My Pipeline** (AE) → "Ready to add to pipeline?" runs **director enrichment** (position preserved),
   then classify: pick a **CRM status** + vet **director-email** guesses → **Save** (`classify_lead`
   writes the ML row + `director_emails`; sets `is_nabd` when CRM status = "Won").
7. **Points/Leaderboard:** `award_activity`; points = `urls_added*25 + leads_saved*50 + (swiped//20)*100`.

## 5. The qualification bar (admin slider)

- One friendly **0–100% slider** in the Admin Control Center maps onto a **30–50 `lead_score` band**:
  **0% → bar 30** (let most real companies through), **50% → bar 40** (default), **100% → bar 50**
  (the strongest realistically reachable). Stored in `app_settings.qualify_percent`; read via
  `settings.get_qualify_bar()`. **Live value is currently 35 (slider 25%).** The band was 30–70 until a
  70 fit score proved unreachable in practice — `QUALIFY_BAR_MAX` was lowered to 50. NB: the DB stores the
  *percent*, the bar is computed in code, so a change to the band only takes effect once `settings.py` is
  deployed (local scripts see it immediately).
- The whole pipeline and the allocation/eligibility queries read this one value, so moving the slider
  changes who qualifies everywhere at once. **If too many decent leads are being screened out, lower the
  slider** rather than touching the scoring.

## 6. Scoring (`scoring.py`) — the most-iterated piece

**`lead_score` = sales fit only** (how good a customer this company is likely to be). It is deliberately
**separate from `confidence_score`** (which is just how sure we are about the website/LinkedIn we found).
Everything calls **`score_lead(LeadFeatures) → int`** and treats it as a black box — that one function is
**the ML seam**: when there's enough `screening_log` data, swap its body for a trained model and no caller
changes.

**Current model (rules) — "minimum = pass, then diminishing".** Each signal (cash, turnover, employees,
debtors, creditors, FX, recent director change) is mapped through a **saturating curve** to a *strength*
in `0…~0.9`: hitting that signal's **minimum** earns ~`0.5` (so that one signal **alone** lands ≈ 50, the
default bar); bigger magnitudes above the minimum add only a little more (diminishing). Strengths then
**combine with diminishing returns**:

```
score = 100 * (1 - (1-s1)(1-s2)(1-s3)...)     # each signal closes part of the gap to 100
```

So: any one minimum passes; a genuinely strong lead climbs toward ~99 **without everyone clamping at a
flat 100**; weak leads stay well below 50; magnitude shows through (e.g. £32m cash scores far above £1m,
but with diminishing steps). **Dormant companies score 0.** **Companies over £30m turnover are kept, not
eliminated** — just scored low on the turnover signal (the other signals can still carry them).

Key constants (easy to tune): `TURNOVER_MIN 1m`, `CASH_MIN 1m`, `EMPLOYEES_MIN 10`, `BALANCE_MIN 500k`
(debtors/creditors), `FX_MIN 100k`, `SEGMENT_TURNOVER_CAP 30m`, `PASS = 0.50`.

**Calibration anchors it currently hits** (from the user's spec):

| Lead | Target | Gets |
|---|---|---|
| 15 staff, nothing else | ~50 | 50 |
| 20 staff, nothing else | ~51 | 51 |
| 50 staff, nothing else | ~10 over a 10-person co | 58 |
| 30 staff + £1m cash + £5m FX | ~99 | 98 |
| 30 staff + £500k cash + £200k FX | ~80 | 84 |
| Each single minimum (turnover £1m / cash £1m / 10 staff / debtors £500k / \|FX\|>£100k) | pass | 50–55 |
| 4 staff + £20k cash; weak micro; dormant | fail | 22 / 7 / 0 |

**Why the previous models were rejected:** a purely **additive** "each minimum = 50, sum, clamp 100" made
*every* lead with two strong signals hit a flat 100 (this was the real cause of the "all my leads are
100/100" complaint — not a display bug; allocation just served the wall of 100s first). A
**diminishing-weights** variant fixed the clamping but penalised broad-but-shallow leads. The current
noisy-OR model is the one that satisfies all of: minimums pass, magnitude matters, and the top end spreads
out instead of clamping.

## 7. Conventions & decisions (incl. this redesign)

- **`lead_score` vs `confidence_score` stay separate** — fit vs data-confidence. Don't merge them.
- **"Start safe":** gates are lenient by design (bin only when even the best case fails); tighten later
  once we trust the data. The 5% **holdout** exists purely to keep the training data unbiased.
- **`account_tier`** (in `scoring.py`, shared with `lead_card`): `total-exemption-full` and `abridged`
  are **SMALL** companies (the word "full" is misleading) — matched before plain `full`/`group`.
- **DDG (DuckDuckGo) search is rate-limited.** `enrichment._ddg_text` paces every search
  (`DDG_PACE = 1.0s` before each call) and retries with backoff (`DDG_RETRIES = 2`, `DDG_BACKOFF = 5s`).
  Best practice is ≥0.75s between calls; the 1s pace also gives the website→LinkedIn gap. If searches time
  out a lot, raise `DDG_PACE`; if it's too slow and not timing out, lower it.
- **My-Pipeline order:** `directors.enrich_lead_directors` writes back the existing `updated_at` so
  enriching a lead doesn't bump it to the top of the AE's list.
- **Session:** cookie-backed, signed token, 10-min sliding idle timeout, session cookie. Brief
  login-screen flash on refresh is a known cookie-component quirk.
- **Passwords:** bcrypt; legacy plaintext auto-upgrades on login. Add users via SQL (lowercase username).
- **"NAB" → "Won"** in the CRM dropdown; underlying column is still `is_nabd`.
- **iXBRL values** honour `scale` (positive only) and `sign`; a float can never reach the int/bigint
  columns (safety net). Cash matching covers `CashBankOnHand`/`CashBankInHand` (the "cashbank" family).

## 8. Local tools

```
python enrich_local.py [N|all]   # run the staged pipeline locally (CH + accounts + web + score)
python rescore_leads.py          # re-score existing leads from stored figures (fast, no internet)
python rerun_pipeline.py         # reset every non-swiped lead and re-run the FULL pipeline (slow)
python test_accounts.py <CRN>    # dump a company's accounts iXBRL tags (parser diagnostic)
```

All write to the same Cloud SQL DB. Don't run a job locally and on the cloud at once (double-processing).
**After any scoring change, run `rescore_leads.py`** (or `rerun_pipeline.py` for a full re-fetch) so
existing leads pick up the new score.

## 9. Current state / where we left off

**The scoring model was the last thing being tuned and has an UNCOMMITTED change awaiting your sign-off.**

Uncommitted in the working tree (the user has accepted the scoring model — these just need committing):
- **`scoring.py`** — the new **noisy-OR "minimum = pass, then diminishing" model** (section 6). Tested
  against all the anchors (table above) and confirmed to behave.
- **`settings.py`** — qualification band changed from **30–70 to 30–50** (a 70 was unreachable).
- **`enrichment.py`** — the **DDG pacing/retry fix** (`_ddg_text`).
- **`rerun_pipeline.py`** — new (untracked) full-re-run utility.
- **`HANDOVER.md`** — this file.

**Settings already applied to the live DB** (these are values, not code, so they're live now):
- Qualification **bar = 35** (slider 25% under the new 30–50 band). Chosen so **BEST OF HUNGARY** (37)
  qualifies — the user considers it a decent company.

**One open tuning question (your call, low priority):**
- The "30 staff + £500k cash + £200k FX" lead lands at **84**; the user had said ~80 — close; can be
  nudged down by softening how much an *above-minimum* FX/cash adds, if wanted nearer 80.

**Next steps after you sign off on the scoring:**
1. Commit the three uncommitted files (scoring + DDG fix + rerun_pipeline).
2. Run **`python rescore_leads.py`** (quick) or **`python rerun_pipeline.py`** (full re-fetch) so the
   existing lead pool gets the new scores. Then re-allocate.

**Recent commits (this redesign), newest last:**
`ed4cb0d` iXBRL cash fix → `708c892`/`900ebb2` indexes/speed → `fb00271` Slice 1: fit score + ML seam →
`e1cae31` qualification-bar slider → `f5b76bf` Slice 2: staged pipeline → `0724ec1` Slice 3: training
logbook + 5% holdout → `5ee366b` rescore_leads → `5a5c655` Slice 4: wire lead_score into allocation +
unify on the pipeline → `77fc1ee` keep My-Pipeline position → `b24bdb1` scoring rework (additive — now
**superseded** by the uncommitted noisy-OR model).

## 10. Open items / known caveats

- **SIC lookup is ~50 common codes** (full CH list ~730). Drop the gov.uk SIC CSV in for a bulk loader.
- **Financial parsing is heuristic** (iXBRL tags + PDF/OCR fallback). Per-field tuning may still be
  needed; `test_accounts.py` dumps the tags for a given CRN to debug.
- **Admin "Latest Leads" preview is capped** (`LIMIT 100`) — a display cap, not a data limit.
- **Most changes were compile-/logic-checked in the dev env** (no live DB/network) — smoke-test on deploy.
- **Second enrichment / Stage B OCR is local-only** (needs the `poppler` + `tesseract` binaries).

## 11. CH Lead Engine — the "High Quality New Incorps" page

A second, self-contained subsystem (all files/tables prefixed `ch_`) that watches Companies House
for **newly incorporated** companies with high expected banking usage (FX, cross-border flows, real
paid-up capital), scores them 0-ish to ~155, and tiers them: **Tier 1 ≥ 60** (high-touch outbound),
**Tier 2 30–59** (sequence), **Tier 3 < 30** (stored, hidden). It optimises *expected transaction
volume × chance of displacing the incumbent bank* — so a foreign-parented NewCo beats a hundred £1
companies, and holding-co/property-SPV vehicles are down-scored. Surfaced on the
**High Quality New Incorps** page (all users; admin gets pipeline controls) and as md/CSV digests.

| File | Responsibility |
|---|---|
| `ch_client.py` | REST client: auth, **shared 550-per-5-min rate limiter**, retries. Smoke test: `python ch_client.py 00000006` |
| `ch_signals.py` | **Pure** signal detection: capital parsing (incl. `associated_filings` fallback), address normalisation + seed formation-agent list, SIC/PSC/officer rules, SPV-farm + quality-director patterns |
| `ch_scoring.py` | **Pure** scoring: the one `WEIGHTS` dict, tiers, event bonus, `top_signals` |
| `ch_enrich.py` | Drain `ch_queue`: fetch profile/PSC/officers/filings, PSC-lag recheck (young co, empty PSC → retry in 48h), serial-director lookups (capped), persist + score. **`sweep_events` = the REST SH01/MR01 detector** (2 calls/company, de-duped by `ref`) that promotes to Tier 1 — this is the only event path |
| `ch_stream.py` | **`python ch_stream.py companies`** — the ONE long-running listener (real-time new incorps → queue; timepoint resume via `ch_stream_state`; **needs CH_STREAM_KEY**). Trigger events come from REST (`sweep_events`), not a filings stream |
| `ch_backfill.py` | `python ch_backfill.py [days] [cap]` — REST ingest via advanced search, **no stream key needed**. The fallback/alternative to the companies stream |
| `ch_run_local.py` | `python ch_run_local.py [N\|all]` — cron entrypoint: drains the queue **then runs the REST event sweep** (mirrors enrich_local.py) |
| `ch_digest.py` | `python ch_digest.py [hours\|all]` — Tier 1/2 digest → `digests/*.md/.csv`; also feeds the page's download buttons |
| `ch_hot_addresses.py` | Monthly: CH bulk snapshot CSV → `ch_hot_addresses` (any address on ≥100 live companies = formation agent, −25) |
| `new_incorps_page.py` | The page: tier/SIC/name/event filters, breakdown drilldown, **Send to swipe pipeline** (inserts into sales_leads as `sourced`), **Suppress** (GDPR list), admin backfill/enrich/digest controls |
| `tests/` | 37 pytest tests + recorded CH fixtures for signals/scoring/capital (`python -m pytest tests`; needs `pip install pytest`) |

Tables (`models.py`, auto-created by `create_all` — no `_ADDED_COLUMNS` needed since they're new):
`ch_companies`, `ch_psc`, `ch_officers`, `ch_capital_statements`, `ch_events`, `ch_scores`
(breakdown JSONB = the audit trail for future weight tuning), `ch_queue`, `ch_stream_state`,
`ch_hot_addresses`, `ch_suppression`.

**Operation (decided 2026-07-06 — stream only for new incorps, REST for events):** the ONLY
streaming process is `python ch_stream.py companies` (real-time new incorporations; run it on an
always-on machine, not Streamlit Cloud). Everything else is REST: `python ch_run_local.py all`
drains the queue and then sweeps for SH01/MR01 trigger events — no /filings stream. Without a
stream key at all, swap the companies stream for `python ch_backfill.py` on a schedule (same
companies, up to a day later). Missing data is always
neutral: no capital figure / no SIC / no PSC yet contributes 0 points, never negative; young
companies with empty PSC lists are automatically rescored ~48h later.

This subsystem deliberately does NOT touch `sales_leads` except via the page's explicit
"Send to swipe pipeline" button, and it shares the CH_API_KEY (the rate limiter keeps it inside
budget). It produces lists only — no outreach.

## 12. Useful SQL

```sql
-- See the screening outcomes of the last pipeline run:
SELECT qualified, is_holdout, count(*) FROM screening_log GROUP BY 1,2;

-- Why were leads screened out?
SELECT screen_reason, count(*) FROM sales_leads WHERE status='screened_out' GROUP BY 1 ORDER BY 2 DESC;

-- Manually set the qualification bar (or just use the admin slider): 50% -> bar 50
INSERT INTO app_settings (key, value) VALUES ('qualify_percent','50')
  ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value;

-- Re-run second enrichment on already-processed leads (then run the pipeline):
UPDATE sales_leads SET second_enriched = NULL WHERE second_enriched = TRUE;

-- Add a user (lowercase username; plaintext self-hashes on first login):
INSERT INTO users (username, password, role) VALUES ('jane', 'TempPass123', 'ae');
```
