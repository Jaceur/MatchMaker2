import random
import re
import time

import requests
import streamlit as st
from google.oauth2 import service_account
from ddgs import DDGS
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from google.cloud.sql.connector import Connector, IPTypes
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, Date, Boolean, DateTime, select, update, delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

# ==========================================
# 1. DATABASE SETUP (Optimized for Import)
# ==========================================
# Wrapping this in Streamlit's cache prevents duplicate connections
# when imported into your app.py frontend.
@st.cache_resource
def get_backend_engine():
    # 1. Read the passport from Streamlit Secrets
    creds = service_account.Credentials.from_service_account_info(
        st.secrets["gcp_service_account"]
    )

    # 2. Hand the passport to the Connector
    connector = Connector(credentials=creds)

    def getconn():
        return connector.connect(
            "enrichmentno:europe-west2:matchmaker-2",
            "pg8000",
            user="postgres",
            password=st.secrets["DB_PASSWORD"],
            db="sales-pipeline",
            ip_type=IPTypes.PUBLIC
        )
    return create_engine("postgresql+pg8000://", creator=getconn, pool_pre_ping=True)

engine = get_backend_engine()
metadata = MetaData()

# Sites we never want to mistake for a company's own website.
# Defined once here, instead of being rebuilt for every single lead.
BLOCKED_DOMAINS = [
    'linkedin.com', 'companieshouse', 'endole.co.uk', 'facebook.com', 'gov.uk',
    'instagram.com', 'twitter.com', 'yelp.co.uk', 'yell.com', 'companycheck.co.uk',
    'sparklane-group', 'theladders.com', 'bloomberg.com', 'wikipedia.org',
    'crunchbase.com', 'pitchbook.com', 'zoominfo.com', 'dunandbradstreet',
    'apollo.io', 'glassdoor', 'suite.endole'
]

# ==========================================
# 2. TABLE SCHEMA
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

metadata.create_all(engine)

# ==========================================
# 3. WORKER FUNCTIONS
# ==========================================
def fetch_and_store_random_batch(max_attempts=10):
    ch_url = "https://api.company-information.service.gov.uk/advanced-search/companies"
    ch_api_key = st.secrets["CH_API_KEY"]

    # A capped loop instead of "while True": if we keep rolling dates with
    # no results, we give up gracefully rather than spinning forever and
    # freezing the app on someone's screen.
    for attempt in range(1, max_attempts + 1):
        days_back = random.randint(365, 7300)
        target_date_obj = datetime.now() - timedelta(days=days_back)
        target_date_str = target_date_obj.strftime("%Y-%m-%d")

        print(f"Attempt {attempt}/{max_attempts}: targeting incorporation date {target_date_str}...")

        response = requests.get(
            ch_url,
            auth=(ch_api_key, ''),
            params={
                "incorporated_from": target_date_str,
                "incorporated_to": target_date_str,
                "company_status": "active",
                "company_type": "ltd,llp",
                "size": 100
            },
            timeout=15  # never wait forever on a stuck network call
        )

        if response.status_code == 404:
            print(" -> 0 active companies found. Rolling a new date...\n")
            time.sleep(0.5)  # be polite to the API between retries
            continue

        if response.status_code != 200:
            print(f" -> API Error: {response.status_code}")
            return 0

        companies_data = response.json().get('items', [])
        print(f" -> Retrieved {len(companies_data)} active records. Committing to Cloud SQL...")

        batch_data = []
        for item in companies_data:
            raw_sic = item.get('sic_codes', [])
            batch_data.append({
                'crn': item.get('company_number'),
                'company_name': item.get('company_name'),
                'incorporation_date': target_date_obj.date(),
                'sic_codes': ", ".join(raw_sic) if raw_sic else None,
                'status': 'sourced'
            })

        new_rows = 0
        if batch_data:
            with engine.begin() as connection:
                insert_stmt = pg_insert(sales_leads).values(batch_data)
                # If the CRN already exists, silently skip it rather than crashing
                do_nothing_stmt = insert_stmt.on_conflict_do_nothing(index_elements=['crn'])
                result = connection.execute(do_nothing_stmt)
                new_rows = result.rowcount
                print(f"Successfully loaded {new_rows} net new rows into sales_leads.")

        return new_rows

    print(f"Gave up after {max_attempts} attempts with no results.")
    return 0

def score_website_match(url, company_name_clean):
    score = 0
    clean_name_lower = company_name_clean.lower()
    first_word = clean_name_lower.split()[0] if clean_name_lower else ""
    domain = url.split('/')[2].lower() if '//' in url else url.lower()

    if clean_name_lower.replace(" ", "") in domain:
        score += 30
    elif first_word and first_word in domain:
        score += 15

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        # Certificate checking stays ON. If a site has a broken security
        # certificate, we simply skip reading its page content — the lead
        # still gets its domain-name score above, just not the page bonus.
        response = requests.get(url, headers=headers, timeout=5)

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            title = soup.title.string.lower() if soup.title and soup.title.string else ""
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            meta_content = meta_desc['content'].lower() if meta_desc and 'content' in meta_desc.attrs else ""
            body_text = soup.get_text(separator=' ', strip=True).lower()[:5000]

            if clean_name_lower in title: score += 40
            if clean_name_lower in meta_content: score += 20
            if clean_name_lower in body_text: score += 20

            if any(bad in title for bad in ['directory', 'company profile', 'job', 'overview', 'wiki']):
                score -= 50
    except Exception:
        pass

    return min(score, 100)

def score_linkedin_match(url, title, snippet, company_name_clean):
    score = 0
    clean_name_lower = company_name_clean.lower()
    clean_name_no_spaces = clean_name_lower.replace(" ", "")
    first_word = clean_name_lower.split()[0] if clean_name_lower else ""

    url_parts = url.rstrip('/').split('/')
    slug = url_parts[-1].lower() if 'company' in url_parts else ""
    slug_no_hyphens = slug.replace("-", "")

    # SIGNAL 1: URL Slug Match (Stricter)
    if clean_name_no_spaces == slug_no_hyphens:
        score += 40  # Exact match
    elif slug_no_hyphens.startswith(clean_name_no_spaces):
        score += 20  # Starts with (e.g., revolut-ltd)
    elif first_word and slug_no_hyphens.startswith(first_word):
        score += 5   # First word only (Severely nerfed)

    # SIGNAL 2: Search Engine Corroboration
    if title and clean_name_lower in title.lower():
        score += 30
    if snippet and clean_name_lower in snippet.lower():
        score += 20

    # SIGNAL 3: Geographic Context (The Companies House Advantage)
    if snippet and any(uk_term in snippet.lower() for uk_term in [' uk ', 'united kingdom', 'london', 'england']):
        score += 10

    # PENALTIES (Aggressive filtering)
    url_lower = url.lower()
    if any(bad in url_lower for bad in ['/showcase/', '/school/', '/pulse/', '/directory/']):
        score -= 50

    # Penalize if the snippet clearly indicates a foreign branch
    if snippet and any(foreign in snippet.lower() for foreign in [' usa ', ' ny ', 'california', 'australia', 'canada']):
        score -= 20

    return min(max(score, 0), 100)  # Ensure it stays between 0 and 100

def enrich_one_lead(record):
    """Does all the slow internet work for a single company and returns
    everything we learned. No database writing happens in here."""
    company_name_strict = re.sub(r'\s+', ' ', record.company_name).strip()
    company_name_clean = re.sub(
        r'\b(LTD|LIMITED|LLP|PLC|UK|HOLDINGS|GROUP|ENTERPRISES|SERVICES)\b',
        '', company_name_strict, flags=re.IGNORECASE
    ).strip()

    print(f"\nEnriching: {company_name_strict}")

    # Start every score at zero BEFORE any internet calls. If a search
    # blows up, these safe defaults still exist and nothing crashes.
    found_domain = None
    best_score = 0
    found_linkedin = None
    best_li_score = 0
    best_li_title = None
    best_li_snippet = None

    # One search session handles both lookups for this lead.
    try:
        with DDGS() as ddgs:
            # --- Website lookup ---
            try:
                results = list(ddgs.text(f'{company_name_clean} UK official website', max_results=3))
                best_url = None
                for result in results:
                    raw_link = result.get('href', '').lower()
                    if any(blocked in raw_link for blocked in BLOCKED_DOMAINS):
                        continue
                    confidence = score_website_match(raw_link, company_name_clean)
                    if confidence > best_score:
                        best_score, best_url = confidence, raw_link
                if best_score >= 40:
                    found_domain = best_url
            except Exception as e:
                print(f"DDG Website Search failed: {e}")

            # --- LinkedIn lookup ---
            try:
                strict_query = f'{company_name_strict} UK site:linkedin.com/company/'
                results = list(ddgs.text(strict_query, max_results=3))
                best_li_url = None
                for result in results:
                    raw_link = result.get('href', '')
                    title = result.get('title', '')
                    snippet = result.get('body', '')
                    if "/company/" in raw_link and "/jobs/" not in raw_link:
                        confidence = score_linkedin_match(raw_link, title, snippet, company_name_clean)
                        if confidence > best_li_score:
                            best_li_score = confidence
                            best_li_url = raw_link.split('?')[0]
                            best_li_title = title
                            best_li_snippet = snippet
                if best_li_score >= 40:
                    found_linkedin = best_li_url
            except Exception as e:
                print(f"DDG LinkedIn Search failed: {e}")
    except Exception as e:
        print(f"Could not start search session: {e}")

    # --- Combine the two scores into one confidence number ---
    web_status = "high" if best_score >= 70 else "low" if best_score >= 40 else "none"
    li_status = "high" if best_li_score >= 70 else "low" if best_li_score >= 40 else "none"
    statuses = [web_status, li_status]

    if statuses.count("high") == 2:
        combined_score = 90
    elif statuses.count("high") == 1 and statuses.count("low") == 1:
        combined_score = 85
    elif statuses.count("high") == 1 and statuses.count("none") == 1:
        combined_score = 80
    elif statuses.count("low") == 2:
        combined_score = 70
    elif statuses.count("low") == 1 and statuses.count("none") == 1:
        combined_score = 60
    else:
        combined_score = 0

    print(f" -> Website: {found_domain} ({web_status})")
    print(f" -> LinkedIn: {found_linkedin} ({li_status})")
    print(f" -> OVERALL SCORE: {combined_score}")

    return {
        "website_url": found_domain,
        "linkedin_url": found_linkedin,
        "linkedin_raw_title": best_li_title,
        "linkedin_raw_snippet": best_li_snippet,
        "confidence_score": combined_score,
        "status": "ready_for_swipe",
    }

def enrich_sourced_leads(limit=None):
    print("Starting enrichment phase...")

    # Step 1: a quick in-and-out trip to the database to get the list.
    with engine.connect() as connection:
        query = select(sales_leads).where(sales_leads.c.status == 'sourced')
        if limit is not None:
            query = query.limit(limit)
        records_to_enrich = connection.execute(query).fetchall()

    if not records_to_enrich:
        print("No new leads to enrich.")
        return 0

    print(f"Found {len(records_to_enrich)} leads to enrich...")

    # Step 2: do the slow internet work one lead at a time, and save each
    # result immediately. If the batch dies at lead 80, leads 1-79 are
    # already safely in the database.
    enriched_count = 0
    for record in records_to_enrich:
        enrichment = enrich_one_lead(record)
        with engine.begin() as connection:
            connection.execute(
                update(sales_leads)
                .where(sales_leads.c.id == record.id)
                .values(**enrichment)
            )
        enriched_count += 1

    print("\nEnrichment batch complete!")
    return enriched_count

def clear_database():
    print("Connecting to database to initiate wipe...")
    with engine.begin() as connection:
        result = connection.execute(delete(sales_leads))
        print(f"SUCCESS: Wiped {result.rowcount} records from the sales_leads table.")
        return result.rowcount

def assign_leads_to_ae(username, num_leads):
    print(f"Assigning {num_leads} leads to {username}...")
    with engine.begin() as connection:
        # We use a subquery to grab the best unassigned leads
        assign_query = text("""
            UPDATE sales_leads
            SET assigned_ae_username = :username,
                assigned_date = :now
            WHERE id IN (
                SELECT id FROM sales_leads
                WHERE status = 'ready_for_swipe' AND assigned_ae_username IS NULL
                ORDER BY confidence_score DESC
                LIMIT :limit
            )
        """)

        result = connection.execute(assign_query, {
            "username": username,
            "now": datetime.utcnow(),
            "limit": num_leads
        })

        return result.rowcount

# ==========================================
# 4. PIPELINE MANAGERS
# ==========================================
def run_sourcing_pipeline():
    print("UI Triggered: Sourcing Pipeline...")
    new_rows = fetch_and_store_random_batch()
    return f"Sourcing complete! {new_rows} new leads added."

def run_enrichment_pipeline():
    print("UI Triggered: Enrichment Pipeline...")
    count = enrich_sourced_leads(limit=100)
    return f"Enrichment complete! {count} leads processed."

def clear_all_data():
    print("UI Triggered: Wiping database...")
    wiped = clear_database()
    return f"Database wiped. {wiped} records deleted."

# ==========================================
# 5. TERMINAL CONTROL PANEL
# ==========================================
def main_menu():
    while True:
        print("\n" + "="*35 + "\n MATCHMAKER 2.0 - CONTROL PANEL\n" + "="*35)
        print("1. Source: Pull 100 companies from a random day\n2. Enrich: Process a batch of 100 leads\n3. Enrich: Process ALL un-enriched leads\n4. Clear:  Wipe entire database table\n5. Exit")
        choice = input("\nEnter your choice (1-5): ").strip()

        if choice == '1': fetch_and_store_random_batch()
        elif choice == '2': enrich_sourced_leads(limit=100)
        elif choice == '3':
            if input("WARNING: This will process ALL 'sourced' leads. Continue? (y/n): ").strip().lower() == 'y': enrich_sourced_leads(limit=None)
        elif choice == '4':
            if input("DANGER: Are you sure you want to completely WIPE the database? (y/n): ").strip().lower() == 'y': clear_database()
        elif choice == '5': break
        else: print("Invalid choice. Please enter 1, 2, 3, 4, or 5.")

if __name__ == "__main__":
    main_menu()