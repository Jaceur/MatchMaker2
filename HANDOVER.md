# Matchmaker 2.0 — Handover

A Streamlit lead-triage app. It sources UK companies from Companies House, enriches them
(website, LinkedIn, financials, trade activity), scores them, and lets Account Executives (AEs)
"swipe" through leads Tinder-style — Pass or Approve — then classify approved leads into
Salesforce CRM statuses. Includes a points/leaderboard system and an admin control centre.

---

## 1. Infra & deployment

- **Host:** Streamlit Cloud, deploys from GitHub **`Jaceur/MatchMaker2`** (branch `main`).
- **Entry point:** `app.py` (Streamlit Cloud → Settings → Main file path must be `app.py`).
- **DB:** Google Cloud SQL (Postgres `sales-pipeline`, instance `enrichmentno:europe-west2:matchmaker-2`),
  reached via the Cloud SQL Python Connector over public IP.
- **Secrets** (`.streamlit/secrets.toml`, gitignored, present locally): `gcp_service_account`,
  `DB_PASSWORD`, `CH_API_KEY`. The auth cookie is signed with an HMAC key derived from `DB_PASSWORD`.
- **Python 3.14** on the deploy env (bleeding edge — watch for missing wheels if adding deps).
- **Run locally:** `streamlit run app.py` (needs the secrets file + `pip install -r requirements.txt`).

## 2. Architecture (file map)

Refactored from a monolith into cohesive modules. Import DAG is acyclic:
`database → models → {auth, leads, sourcing, enrichment, directors, scoring, sic_data} →
{swipe_page, ae_dashboard, ae_home, admin_panel, leaderboard, lead_card} → app`.

| File | Responsibility |
|---|---|
| `app.py` | Entry: page config, password migration + SIC seed (once per process), cookie session, **routing** |
| `database.py` | The one `engine` + `metadata` (Cloud SQL connector), imported everywhere |
| `models.py` | **All table schemas** + `create_all` + auto-migration block (`_ADDED_COLUMNS`) |
| `auth.py` | Password verify/upgrade/migrate, `login_page`, cookie-token session, `change_password`, login throttle |
| `leads.py` | `get_pending_leads`, `build_ml_row`, `engineer_ml_features`, `assign_leads_to_ae`, clear DB / clear pipeline, `award_activity`, **`TIER_THRESHOLD = 60`** |
| `sourcing.py` | Companies House advanced-search → new `sourced` leads |
| `enrichment.py` | DDG website/LinkedIn search + scoring, CH signals (account_type, director change), UK Trade Info (import/export), calls `score_lead` |
| `scoring.py` | `score_lead` — composite 0-100 (account 40 / confidence 30 / trade 20 / directors 10) |
| `directors.py` | CH officers API (3 youngest directors → "First Last"), email-format candidates, `domain_from_url` |
| `second_enrichment.py` | Parses filed **accounts documents** (iXBRL + PDF/OCR) → employee count + financials |
| `swipe_page.py` | `main_app` — the swipe card; validity toggles; `pass_control`; correction inputs |
| `ae_dashboard.py` | **My Pipeline** (`render_ae_pipeline`): classify cards, `classify_lead` |
| `ae_home.py` | **AE Dashboard** (personal: pipeline count, into-Salesforce, points, change password) |
| `admin_panel.py` | **Admin Control Center**: sourcing/enrichment/clear buttons, lead allocation, pipeline-health metrics |
| `leaderboard.py` | AE leaderboard + `compute_points` |
| `lead_card.py` | **Shared `render_profile`** (the Tinder/Revolut card) used by swipe + classify cards |
| `sic_data.py` | SIC code reference dict + loader + cached `get_sic_lookup` |
| `cli.py` | Terminal control panel (source/enrich/clear) |
| `enrich_local.py` (+ `.bat`) | **Local** first-enrichment runner (faster, no cloud timeout) |
| `second_enrich_local.py` | **Local** second-enrichment runner (needs poppler + Tesseract for OCR) |

## 3. Database tables

- **`sales_leads`** — the working lead pool. Key columns: `crn`, `company_name`, `incorporation_date`,
  `sic_codes`, `website_url`/`linkedin_url` (+ `corrected_*`, `*_accurate`), `status`
  (`sourced`→`ready_for_swipe`→`approved`/`archived`), `assigned_ae_username`, `confidence_score`
  (the tier driver), `website_score`/`linkedin_score`, `lead_score`, `account_type`,
  `director_change_recent`/`last_director_change`, `import_activity`/`export_activity`,
  `active_directors`/`directors_enriched`, `employee_count` + financials (`turnover`, `cash_at_bank`,
  `foreign_exchange`, `trade_debtors`, `trade_creditors`, `admin_expenses`, `bank_loans_overdrafts`),
  `second_enriched`, `is_nabd` (**now means "Won"**), `rejection_reason`.
- **`ml_pipeline_analytics`** — ML training log; one row per Pass (`is_worth_it=False`) or classify
  (`is_worth_it=True`). Built by `leads.build_ml_row`.
- **`users`** — login (`id`, `username` lowercase, `password` bcrypt, `role`: `admin` or `ae`).
- **`pipeline_archive`** — snapshot of approved leads taken on "Clear Pipeline" (mirrors `sales_leads`).
- **`director_emails`** — one row per (director × email-format guess) + the AE's X/Y verdict.
- **`ae_stats`** — per-AE counters (`urls_added`, `leads_swiped`, `leads_saved`) → leaderboard points.
- **`sic_lookup`** — SIC code → description reference (seeded by `sic_data.py`; ~50 common codes loaded).

**Migrations:** `models.py` runs `create_all` (creates missing **tables**) and an idempotent
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for the columns in `_ADDED_COLUMNS`
(import/export_activity, lead_score, employee_count, second_enriched, and the 7 financial BIGINTs).
**So new columns no longer need manual SQL — add them to `models.py` + `_ADDED_COLUMNS`.**
(A handful of earlier columns — `account_type`, `last_director_change`, `director_change_recent`,
`corrected_*`, `directors_enriched`, `website_score`, `linkedin_score` — were ALTERed by hand before
the auto-migration existed; they're present in the live DB. A fresh DB gets everything from `create_all`.)

## 4. Lead lifecycle

1. **Source** (admin) → `sales_leads` `status='sourced'`.
2. **Enrich** (admin button or `enrich_local.py`) → website + LinkedIn (DDG search + scoring; LinkedIn
   also harvested from the company's own site), CH `account_type` + recent-director-change, UK Trade
   Info import/export, then `lead_score`. Sets `status='ready_for_swipe'`, `confidence_score`.
   **Dormant companies are forced to `confidence_score=0`** (→ Tier 4).
3. **Tiering:** `confidence_score > TIER_THRESHOLD(=60)` = **Tier 3+** (eligible for AEs);
   `≤ 60` = **Tier 4** (held back — never allocated or swiped). Lower the threshold to release more.
4. **Allocate** (admin) → assign Tier 3+ unassigned leads to an AE.
5. **Swipe** (AE, `swipe_page`) → validate website/LinkedIn (correct/incorrect + type a correction),
   then **Pass** (pick a reason → archive + ML log) or **Approve** (`status='approved'`).
6. **My Pipeline** (AE, `ae_dashboard`) → "Ready to add to pipeline?" gate runs **director enrichment**,
   then the classify card: pick a **CRM status** + vet the **director email** guesses → **Save**
   (`classify_lead` writes the ML row + `director_emails` + sets `is_nabd` when CRM status = "Won").
7. **Second enrichment** (`second_enrich_local.py`) → parses the filed accounts document for Tier 3
   leads → `employee_count` + financials.
8. **Points/Leaderboard:** `award_activity` increments counters; points =
   `urls_added*25 + leads_saved*50 + (leads_swiped // 20)*100`.

## 5. Conventions & decisions

- **Session:** cookie-backed (`extra-streamlit-components`). Signed token, **10-min sliding idle**
  timeout, session cookie (clears on browser close — best-effort; the timeout is the reliable backstop).
  Survives refresh. Brief login-screen flash on refresh is a known cookie-component quirk.
- **Passwords:** bcrypt; legacy plaintext supported + auto-upgraded on login; `migrate_plaintext_passwords`
  runs once at boot to purge cleartext. Add users via SQL (lowercase username, role `ae`/`admin`); a
  plaintext password works and self-hashes, or pre-hash with bcrypt.
- **"NAB" was renamed to "Won"** in the CRM dropdown; the underlying column is still `is_nabd`.
- **Local runners** for enrichment because it's slow + CH rate-limited (600 req / 5 min). Second
  enrichment is **local-only** (OCR needs the `poppler` + `tesseract` system binaries; `pdfplumber`/
  `pdf2image`/`pytesseract` are in requirements but unused on cloud).
- **Card UI:** `lead_card.render_profile` is shared by swipe + classify cards (Revolut-grey gradient
  banner, chips `Size · Import · Export · Director change`, metric tiles, financials grid, SIC codes).
  CSS lives in `lead_card.CARD_CSS`.
- **iXBRL values** honour `scale` (positive only) and `sign`; a float can never reach the int/bigint
  columns (safety net in `second_enrich_lead`).

## 6. Local tools

```
python enrich_local.py [N|all]          # first enrichment (website/LinkedIn/CH/trade/score)
python second_enrich_local.py [N|all]   # accounts parsing (employee count + financials), Tier 3 only
python cli.py                           # terminal menu: source / enrich / clear
```
Both write to the same Cloud SQL DB, so results appear in the app. Don't run a job locally and on the
cloud at once (they'd double-process the `sourced`/Tier-3 pools).

## 7. Current state / where we left off

**Actively debugging:** turnover (and other P&L items) showing `—` after second enrichment.
**Diagnosis:** small/micro UK companies file **filleted/abridged accounts with no Profit & Loss**, so
turnover/admin_expenses/FX are legitimately absent — while balance-sheet items (cash, trade
debtors/creditors, bank loans) are present. Just added a `[fin]` debug line to `second_enrichment`
that prints, per field, `value` / `?(label,no num)` / `absent`, plus a `[fin/ixbrl]` line.
**Next step the user is doing:** reset a few leads and re-run to read the `[fin]` lines —
- mostly `absent` → expected (no P&L disclosed), nothing to fix;
- `?(label,no num)` on a full-accounts company → a real parsing bug to tune.

**Just fixed:** iXBRL negative-`scale` bug that made `employee_count = 0.04` and crashed the INTEGER
write (now only positive scale is applied + a float→int safety net).

## 8. Open items / known caveats

- **SIC lookup is ~50 common codes**; the full CH list is ~730. Drop the official gov.uk SIC CSV in and
  ask for a one-line bulk loader for full coverage. `get_sic_lookup` falls back to the in-memory seed.
- **Revolut theme** is only the card banner. Full dark app theme would be a `.streamlit/config.toml`
  `[theme]` change (affects every page) — not done.
- **Financial PDF parsing is heuristic** (label + first non-year number, bracket/NIL handling, £'000
  scaling document-level). Per-field tuning likely needed; iXBRL takes the first matching tag (usually
  current period). Money columns are BIGINT.
- **Classify cards are tall** now (full profile + up to 3 directors × 5 email checkboxes). A compact
  variant is available if wanted.
- **Most changes were compile-checked only** in the dev env (no live DB/network) — smoke-test on deploy.
- Pre-existing data note: `schema-no-migrations` memory — historically adding a column needed a manual
  idempotent ALTER; the `_ADDED_COLUMNS` block now automates that.

## 9. Useful SQL

```sql
-- Re-run second enrichment on already-processed leads (then run second_enrich_local.py):
UPDATE sales_leads SET second_enriched = NULL WHERE second_enriched = TRUE;

-- Push existing dormant leads down to Tier 4 (newly-enriched ones are handled automatically):
UPDATE sales_leads SET confidence_score = 0 WHERE account_type ILIKE '%dormant%' AND confidence_score > 60;

-- Add a user (lowercase username; plaintext self-hashes on first login):
INSERT INTO users (username, password, role) VALUES ('jane', 'TempPass123', 'ae');
```
