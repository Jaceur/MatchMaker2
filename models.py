"""Schema layer: every table the app uses, declared on the shared MetaData.

All tables live here so `create_all` runs exactly once, in one pass, after the
whole schema is known.
"""
from datetime import datetime

from sqlalchemy import (
    Table, Column, Integer, BigInteger, String, Date, Boolean, DateTime, text,
)

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

# Every table is now declared — build them all in one shot. Safe to run on each
# boot: it only creates tables that don't already exist.
metadata.create_all(engine)

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
