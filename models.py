"""Schema layer: every table the app uses, declared on the shared MetaData.

All tables live here so `create_all` runs exactly once, in one pass, after the
whole schema is known.
"""
from datetime import datetime

from sqlalchemy import (
    Table, Column, Integer, String, Date, Boolean, DateTime,
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
    Column('rejection_reason', String(255)),
    Column('is_nabd', Boolean, default=False),
    Column('active_directors', String(255)),
    Column('linkedin_raw_title', String),
    Column('linkedin_raw_snippet', String),
    Column('status', String(50), default='sourced'),
    Column('assigned_ae_username', String(100)),
    Column('assigned_date', DateTime),
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

# Every table is now declared — build them all in one shot. Safe to run on each
# boot: it only creates tables that don't already exist.
metadata.create_all(engine)
