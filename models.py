"""Schema layer: every table the app uses, declared on the shared MetaData.

All tables live here so `create_all` runs exactly once, in one pass, after the
whole schema is known.
"""
from datetime import datetime

from sqlalchemy import (
    Table, Column, Integer, BigInteger, String, Date, Boolean, DateTime, Numeric,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from database import engine, metadata

# ==========================================
# THE LIVE PIPELINE
# ==========================================
sales_leads = Table(
    'sales_leads', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('crn', String(20), unique=True, nullable=False),
    Column('company_name', String(255), nullable=False),
    Column('incorporation_date', Date),
    Column('sic_codes', String(255)),
    Column('website_url', String(500)),
    Column('linkedin_url', String(500)),
    Column('contact_email', String(255)),
    Column('website_accurate', Boolean, default=None),
    Column('linkedin_accurate', Boolean, default=None),
    Column('contact_accurate', Boolean, default=None),
    # Human-supplied replacements, filled in when a source is marked Incorrect
    # on the swipe screen.
    Column('corrected_website_url', String(500)),
    Column('corrected_linkedin_url', String(500)),
    Column('rejection_reason', String(255)),
    Column('is_nabd', Boolean, default=False),
    Column('active_directors', String(255)),
    Column('directors_enriched', Boolean, default=False),
    # Companies House extras pulled during enrichment.
    Column('account_type', String(50)),               # micro-entity / small / medium / full…
    Column('last_director_change', Date),             # most recent AP01/TM01 filing date
    Column('director_change_recent', Boolean),        # within the last ~6 months
    Column('import_activity', Boolean),               # appears as an importer in HMRC UK Trade Info
    Column('export_activity', Boolean),               # appears as an exporter in HMRC UK Trade Info
    Column('lead_score', Integer),                    # composite 0-100 base score (scoring.py)
    Column('employee_count', Integer),                # parsed from the filed accounts (second enrichment)
    # Financials parsed from the accounts (second enrichment). BigInteger as
    # turnover etc. can exceed the 32-bit INT range; foreign_exchange is signed.
    Column('turnover', BigInteger),
    Column('cash_at_bank', BigInteger),
    Column('foreign_exchange', BigInteger),
    Column('trade_debtors', BigInteger),
    Column('trade_creditors', BigInteger),
    Column('admin_expenses', BigInteger),
    Column('bank_loans_overdrafts', BigInteger),
    Column('second_enriched', Boolean),              # accounts document processed?
    Column('linkedin_raw_title', String),
    Column('linkedin_raw_snippet', String),
    Column('status', String(50), default='sourced'),
    Column('screen_reason', String(255)),            # why the pipeline screened a lead out (stage + reason)
    Column('is_holdout', Boolean),                    # bypassed the gates for unbiased training data
    Column('assigned_ae_username', String(100)),
    Column('assigned_date', DateTime),
    # Per-source scraper scores (0-100) kept distinct from the combined
    # confidence_score, so the ML log can use three independent signals.
    Column('website_score', Integer),
    Column('linkedin_score', Integer),
    Column('confidence_score', Integer, default=0),
    Column('created_at', DateTime, default=datetime.utcnow),
    Column('updated_at', DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
)

# ==========================================
# THE ML TRAINING LOG
# ==========================================
ml_pipeline_analytics = Table(
    'ml_pipeline_analytics', metadata,
    Column('id', Integer, primary_key=True),
    Column('lead_id', Integer),
    Column('crn', String),

    # Firmographics
    Column('company_age_months', Integer),
    Column('director_count', Integer),

    # Scraper Scores
    Column('website_score', Integer),
    Column('linkedin_score', Integer),
    Column('overall_score', Integer),

    # Human Validations
    Column('website_valid', Boolean),
    Column('linkedin_valid', Boolean),
    Column('corrected_website_url', String),
    Column('corrected_linkedin_url', String),

    # The Swipe
    Column('is_worth_it', Boolean),
    Column('rejection_reason', String),
    Column('dwell_time_seconds', Integer),

    # The CRM Reality
    Column('crm_status', String),

    # Audit
    Column('swiped_by', String),
    Column('created_at', DateTime, default=datetime.utcnow)
)

# ==========================================
# LOGIN / ACCESS
# ==========================================
users_table = Table(
    'users', metadata,
    Column('id', Integer, primary_key=True),
    Column('username', String),
    Column('password', String),
    Column('role', String),
)

# ==========================================
# PIPELINE ARCHIVE
# ==========================================
# A permanent home for approved ("won") leads. "Clear Pipeline" snapshots
# approved rows in here before removing them from the live table, so they
# survive a wipe. Mirrors sales_leads' data columns but drops the unique
# constraints (an archive may legitimately hold historical duplicates).
pipeline_archive = Table(
    'pipeline_archive', metadata,
    Column('archive_id', Integer, primary_key=True, autoincrement=True),
    Column('id', Integer),                 # original sales_leads id
    Column('crn', String(20)),
    Column('company_name', String(255)),
    Column('incorporation_date', Date),
    Column('sic_codes', String(255)),
    Column('website_url', String(500)),
    Column('linkedin_url', String(500)),
    Column('contact_email', String(255)),
    Column('website_accurate', Boolean),
    Column('linkedin_accurate', Boolean),
    Column('contact_accurate', Boolean),
    Column('corrected_website_url', String(500)),
    Column('corrected_linkedin_url', String(500)),
    Column('rejection_reason', String(255)),
    Column('is_nabd', Boolean),
    Column('active_directors', String(255)),
    Column('directors_enriched', Boolean),
    Column('account_type', String(50)),
    Column('last_director_change', Date),
    Column('director_change_recent', Boolean),
    Column('import_activity', Boolean),
    Column('export_activity', Boolean),
    Column('lead_score', Integer),
    Column('employee_count', Integer),
    Column('turnover', BigInteger),
    Column('cash_at_bank', BigInteger),
    Column('foreign_exchange', BigInteger),
    Column('trade_debtors', BigInteger),
    Column('trade_creditors', BigInteger),
    Column('admin_expenses', BigInteger),
    Column('bank_loans_overdrafts', BigInteger),
    Column('second_enriched', Boolean),
    Column('linkedin_raw_title', String),
    Column('linkedin_raw_snippet', String),
    Column('status', String(50)),
    Column('screen_reason', String(255)),
    Column('is_holdout', Boolean),
    Column('assigned_ae_username', String(100)),
    Column('assigned_date', DateTime),
    Column('website_score', Integer),
    Column('linkedin_score', Integer),
    Column('confidence_score', Integer),
    Column('created_at', DateTime),
    Column('updated_at', DateTime),
    Column('archived_at', DateTime, default=datetime.utcnow),
)

# ==========================================
# DIRECTOR EMAIL CANDIDATES
# ==========================================
# One row per (director × email-format guess) with the AE's X/Y verdict, for
# later analysis of which patterns are right. Links back to a lead via
# lead_id / crn.
director_emails = Table(
    'director_emails', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('lead_id', Integer),
    Column('crn', String),
    Column('director_name', String),
    Column('pattern', String),       # e.g. 'first.last'
    Column('email', String),
    Column('selected', Boolean),     # the X/Y choice: True = Y (looks right)
    Column('swiped_by', String),
    Column('created_at', DateTime, default=datetime.utcnow),
)

# ==========================================
# AE ACTIVITY / POINTS
# ==========================================
# Running per-AE counters that drive the leaderboard. Points are derived from
# these on the leaderboard page (urls*25 + saves*50 + (swipes // 20)*100).
ae_stats = Table(
    'ae_stats', metadata,
    Column('username', String, primary_key=True),
    Column('urls_added', Integer, default=0),
    Column('leads_swiped', Integer, default=0),
    Column('leads_saved', Integer, default=0),
)

# ==========================================
# SIC CODE REFERENCE
# ==========================================
# Companies House nature-of-business codes -> human description. Seeded from
# sic_data.py; the swipe card translates a lead's sic_codes against this.
sic_lookup = Table(
    'sic_lookup', metadata,
    Column('code', String(10), primary_key=True),
    Column('description', String(255)),
)

# ==========================================
# RUNTIME SETTINGS (key/value)
# ==========================================
# Small store for values tuned at runtime from the admin dashboard (today: the
# lead-qualification bar). A brand-new table, so create_all builds it for us —
# no ADD COLUMN migration needed.
app_settings = Table(
    'app_settings', metadata,
    Column('key', String(50), primary_key=True),
    Column('value', String(255)),
)

# ==========================================
# SCREENING LOG (ML training data)
# ==========================================
# One row per lead the staged pipeline processes: the features the score was
# based on + the score + the decision + the holdout flag. A durable snapshot
# (survives a later re-enrichment) that pairs with the AE's eventual verdict in
# ml_pipeline_analytics (by lead_id) to train the future scoring model. New
# table, so create_all builds it automatically.
screening_log = Table(
    'screening_log', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('lead_id', Integer),
    Column('crn', String(20)),
    Column('company_name', String(255)),
    # the features (the future model's inputs):
    Column('account_type', String(50)),
    Column('employee_count', Integer),
    Column('turnover', BigInteger),
    Column('cash_at_bank', BigInteger),
    Column('foreign_exchange', BigInteger),
    Column('trade_debtors', BigInteger),
    Column('trade_creditors', BigInteger),
    Column('import_activity', Boolean),
    Column('export_activity', Boolean),
    Column('director_change_recent', Boolean),
    # the decision:
    Column('lead_score', Integer),
    Column('qualify_bar', Integer),
    Column('qualified', Boolean),
    Column('is_holdout', Boolean),
    Column('screen_reason', String(255)),
    Column('created_at', DateTime, default=datetime.utcnow),
)

# ==========================================
# CH LEAD ENGINE ("High Quality New Incorps")
# ==========================================
# A separate subsystem that watches Companies House for NEWLY INCORPORATED
# companies with high expected banking usage (FX, cross-border flows, real
# capital). It has its own tables (all prefixed ch_) because it tracks a
# different universe than sales_leads: thousands of brand-new companies per
# day, most of which never become leads. A scored Tier 1/2 company can be
# promoted into sales_leads from the "High Quality New Incorps" page.
# All-new tables, so create_all builds them — no _ADDED_COLUMNS entries needed.

# One row per company seen on the stream / backfill. company_number is the
# natural key everywhere in this subsystem (same thing sales_leads calls crn).
ch_companies = Table(
    'ch_companies', metadata,
    Column('company_number', String(20), primary_key=True),
    Column('name', String(255)),
    Column('date_of_creation', Date, index=True),
    Column('status', String(50)),
    Column('type', String(50)),
    Column('sic_codes', String(255)),            # comma-joined, like sales_leads
    Column('registered_address', JSONB),
    Column('address_normalised', String(500)),
    Column('first_seen_at', DateTime, default=datetime.utcnow),
    Column('enriched_at', DateTime),
    # PSC data can lag incorporation by days: when the PSC list is empty for a
    # very young company we come back after this time instead of finalising.
    Column('recheck_after', DateTime),
)

# Persons with significant control — the foreign-parent signal lives here.
ch_psc = Table(
    'ch_psc', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('company_number', String(20), index=True),
    Column('kind', String(100)),
    Column('name', String(255)),
    Column('country', String(100)),
    Column('raw', JSONB),
)

ch_officers = Table(
    'ch_officers', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('company_number', String(20), index=True),
    Column('officer_id', String(50)),
    Column('name', String(255)),
    Column('role', String(50)),
    Column('correspondence_country', String(100)),
    Column('prior_appointments', Integer),
    Column('quality_flag', String(20)),          # 'quality' | 'spv_farm' | 'unknown'
    Column('raw', JSONB),
)

# Statements of capital parsed from filings (NEWINC at incorporation, SH01 on a
# later allotment). figure is NUMERIC because CH publishes decimals.
ch_capital_statements = Table(
    'ch_capital_statements', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('company_number', String(20), index=True),
    Column('filing_type', String(20)),           # NEWINC | SH01 | ...
    Column('currency', String(10)),
    Column('figure', Numeric(18, 2)),
    Column('filing_date', Date),
    Column('raw', JSONB),
)

# Post-incorporation trigger events (SH01 fresh raise, MR01 debt financing)
# spotted on the filings stream. These promote a company to Tier 1.
ch_events = Table(
    'ch_events', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('company_number', String(20), index=True),
    Column('event_type', String(30)),            # sh01_raise | mr01_charge
    Column('detail', JSONB),
    Column('occurred_at', DateTime, default=datetime.utcnow),
    Column('actioned', Boolean, default=False),
)

# One score row per company, with the full signal breakdown kept as JSONB so
# weights can be re-tuned later without re-enriching anything.
ch_scores = Table(
    'ch_scores', metadata,
    Column('company_number', String(20), primary_key=True),
    Column('score', Integer),
    Column('tier', Integer, index=True),
    Column('breakdown', JSONB),
    Column('scored_at', DateTime),
)

# The work queue between ingest (stream/backfill) and enrichment. A status
# column on a table is plenty at ~3,000 incorporations/day — no broker needed.
ch_queue = Table(
    'ch_queue', metadata,
    Column('company_number', String(20), primary_key=True),
    Column('stage', String(20), default='new', index=True),   # new | scored | failed
    Column('attempts', Integer, default=0),
    Column('last_error', String(500)),
    Column('updated_at', DateTime, default=datetime.utcnow),
)

# Last processed timepoint per stream, so a restarted listener resumes exactly
# where it left off (no gaps, dupes handled by upserts).
ch_stream_state = Table(
    'ch_stream_state', metadata,
    Column('stream', String(20), primary_key=True),           # companies | filings
    Column('timepoint', BigInteger),
    Column('updated_at', DateTime),
)

# Registered-office addresses used by formation agents (appearing on hundreds
# of companies). Seeded from a hardcoded list; refreshed monthly from the CH
# bulk snapshot via ch_hot_addresses.py.
ch_hot_addresses = Table(
    'ch_hot_addresses', metadata,
    Column('address_normalised', String(500), primary_key=True),
    Column('company_count', Integer),
    Column('refreshed_at', DateTime),
)

# GDPR/PECR suppression list: companies (and thereby their directors) we must
# not surface in digests or on the page.
ch_suppression = Table(
    'ch_suppression', metadata,
    Column('company_number', String(20), primary_key=True),
    Column('reason', String(255)),
    Column('created_at', DateTime, default=datetime.utcnow),
)

# "Add to pipe" claims. One row = one company claimed by one user. company_number
# is the PK, so a company can only be claimed once (first-come-first-served — this
# is a race); claiming removes it from everyone else's board.
ch_claims = Table(
    'ch_claims', metadata,
    Column('company_number', String(20), primary_key=True),
    Column('username', String(100)),
    Column('claimed_at', DateTime, default=datetime.utcnow),
)

# "Pass" dismissals — PER USER (composite key), so one rep passing a lead only
# hides it from their own board, not the whole team's.
ch_passes = Table(
    'ch_passes', metadata,
    Column('company_number', String(20), primary_key=True),
    Column('username', String(100), primary_key=True),
    Column('passed_at', DateTime, default=datetime.utcnow),
)

# Every table is now declared — build them all in one shot. Safe to run on each
# boot: it only creates tables that don't already exist.
#
# Wrapped in try/except on purpose. The Table(...) objects above register
# themselves on the shared `metadata` (which lives in database.py) the moment
# they're constructed — BEFORE this line runs. If create_all raised (e.g. the
# Cloud SQL instance is briefly stopped/restarting), this module's import would
# fail *after* the tables were already registered on that long-lived metadata.
# Streamlit then re-runs the script, re-imports this module, and the first
# `Table('sales_leads', metadata, ...)` blows up with "Table is already defined
# for this MetaData instance" — wedging the ENTIRE app (even the login page)
# until the process is rebooted. Swallowing the error here lets the app finish
# importing and degrade gracefully: existing tables already exist, and any
# brand-new tables get created on the next boot once the database is reachable.
try:
    metadata.create_all(engine)
except Exception as _e:
    print(f"create_all skipped (database unreachable at boot?): {_e}")

# create_all only CREATEs missing tables — it never adds a column to a table
# that already exists. These three columns were introduced after sales_leads and
# pipeline_archive were already live, so bring existing databases up to date with
# an idempotent ADD COLUMN IF NOT EXISTS (Postgres). Wrapped so a migration
# hiccup can never block boot, mirroring the password migration in app.py.
_ADDED_COLUMNS = {
    "import_activity": "BOOLEAN",
    "export_activity": "BOOLEAN",
    "lead_score": "INTEGER",
    "employee_count": "INTEGER",
    "turnover": "BIGINT",
    "cash_at_bank": "BIGINT",
    "foreign_exchange": "BIGINT",
    "trade_debtors": "BIGINT",
    "trade_creditors": "BIGINT",
    "admin_expenses": "BIGINT",
    "bank_loans_overdrafts": "BIGINT",
    "second_enriched": "BOOLEAN",
    "screen_reason": "VARCHAR(255)",
    "is_holdout": "BOOLEAN",
}
try:
    with engine.begin() as _conn:
        for _table_name in ("sales_leads", "pipeline_archive"):
            for _col, _col_type in _ADDED_COLUMNS.items():
                _conn.execute(text(
                    f"ALTER TABLE {_table_name} "
                    f"ADD COLUMN IF NOT EXISTS {_col} {_col_type}"
                ))
except Exception as _e:
    print(f"Schema migration (trade/lead_score columns) skipped: {_e}")

# screening_log gained trade_debtors / trade_creditors once they became scoring
# inputs; create_all won't add columns to the already-created table, so do it
# idempotently here.
try:
    with engine.begin() as _conn:
        for _col in ("trade_debtors", "trade_creditors"):
            _conn.execute(text(
                f"ALTER TABLE screening_log ADD COLUMN IF NOT EXISTS {_col} BIGINT"
            ))
except Exception as _e:
    print(f"screening_log migration skipped: {_e}")

# Indexes for the columns the latency-sensitive queries filter and sort by: the
# swipe queue (get_pending_leads) and lead allocation (assign_leads_to_ae) both
# filter on status + assigned_ae_username and order by confidence_score, none of
# which was indexed — so each query scanned the whole table. As with the columns
# above, create_all won't add an index to an existing table, so do it idempotently.
# CREATE INDEX IF NOT EXISTS is a no-op once the index exists; it adds no data,
# only a faster lookup path, and can be dropped again with no data loss.
_INDEXES = {
    "ix_sales_leads_status_ae_score":
        "sales_leads (status, assigned_ae_username, confidence_score)",
}
try:
    with engine.begin() as _conn:
        for _ix_name, _ix_target in _INDEXES.items():
            _conn.execute(text(f"CREATE INDEX IF NOT EXISTS {_ix_name} ON {_ix_target}"))
except Exception as _e:
    print(f"Index migration skipped: {_e}")
