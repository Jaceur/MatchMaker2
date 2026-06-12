import random
import requests
import re
import urllib3
import streamlit as st
from google.oauth2 import service_account
from ddgs import DDGS
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from google.cloud.sql.connector import Connector, IPTypes
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, Date, Boolean, DateTime, select, update, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
def fetch_and_store_random_batch():
    ch_url = "https://api.company-information.service.gov.uk/advanced-search/companies"
    ch_api_key = st.secrets["CH_API_KEY"]  # Pulled securely!

    while True:
        days_back = random.randint(365, 7300)
        target_date_obj = datetime.now() - timedelta(days=days_back)
        target_date_str = target_date_obj.strftime("%Y-%m-%d")
        
        print(f"Targeting incorporation date: {target_date_str}...")
        
        response = requests.get(
            ch_url, 
            auth=(ch_api_key, ''), 
            params={
                "incorporated_from": target_date_str,
                "incorporated_to": target_date_str,
                "company_status": "active",
                "company_type": "ltd,llp",
                "size": 100  
            }
        )
        
        if response.status_code == 404:
            print(" -> 0 active companies found. Rolling a new date...\n")
            continue
            
        if response.status_code != 200:
            print(f" -> API Error: {response.status_code}")
            return

        companies_data = response.json().get('items', [])
        print(f" -> Retrieved {len(companies_data)} active records. Committing to Cloud SQL...")

        # OPTIMIZATION: High-speed bulk insert using PostgreSQL native conflict resolution
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

        if batch_data:
            with engine.begin() as connection:
                insert_stmt = pg_insert(sales_leads).values(batch_data)
                # If the CRN already exists, silently skip it rather than crashing
                do_nothing_stmt = insert_stmt.on_conflict_do_nothing(index_elements=['crn'])
                result = connection.execute(do_nothing_stmt)
                print(f"Successfully loaded {result.rowcount} net new rows into sales_leads.")
            
        break

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
        response = requests.get(url, headers=headers, timeout=5, verify=False)
        
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

    # Extract the company slug from the URL (e.g., /company/revolut -> revolut)
    url_parts = url.rstrip('/').split('/')
    slug = url_parts[-1].lower() if 'company' in url_parts else ""
    slug_no_hyphens = slug.replace("-", "")

    # SIGNAL 1: URL Slug Match (Max 50 points)
    if clean_name_no_spaces == slug_no_hyphens:
        score += 50  # Exact match
    elif clean_name_no_spaces in slug_no_hyphens or (slug_no_hyphens and slug_no_hyphens in clean_name_no_spaces):
        score += 25  # Partial match
    elif first_word and first_word in slug_no_hyphens:
        score += 10  # First word only

    # SIGNAL 2: Search Engine Snippet Corroboration (Max 50 points)
    if title and clean_name_lower in title.lower():
        score += 30
    if snippet and clean_name_lower in snippet.lower():
        score += 20

    # PENALTIES (Kill bad directory links)
    if "directory" in url.lower() or "showcase" in url.lower():
        score -= 50

    return min(score, 100)

def enrich_sourced_leads(limit=None):
    print("Starting enrichment phase...")
    with engine.begin() as connection:
        query = select(sales_leads).where(sales_leads.c.status == 'sourced')
        if limit is not None:
            query = query.limit(limit)
            
        records_to_enrich = connection.execute(query).fetchall()
        
        if not records_to_enrich:
            print("No new leads to enrich.")
            return

        print(f"Found {len(records_to_enrich)} leads to enrich...")

        for record in records_to_enrich:
            company_name_strict = re.sub(r'\s+', ' ', record.company_name).strip()
            company_name_clean = re.sub(
                r'\b(LTD|LIMITED|LLP|PLC|UK|HOLDINGS|GROUP|ENTERPRISES|SERVICES)\b', 
                '', company_name_strict, flags=re.IGNORECASE
            ).strip()
            
            print(f"\nEnriching: {company_name_strict}")
            
            # Domain Lookup
            found_domain = None
            try:
                with DDGS() as ddgs:
                    results = list(ddgs.text(f'{company_name_clean} UK official website', max_results=3))
                    best_score, best_url = 0, None
                    blocked_domains = ['linkedin.com', 'companieshouse', 'endole.co.uk', 'facebook.com', 'gov.uk', 'instagram.com', 'twitter.com', 'yelp.co.uk', 'yell.com', 'companycheck.co.uk', 'sparklane-group', 'theladders.com', 'bloomberg.com', 'wikipedia.org', 'crunchbase.com', 'pitchbook.com', 'zoominfo.com', 'dunandbradstreet', 'apollo.io', 'glassdoor', 'suite.endole']
                    
                    for result in results:
                        raw_link = result.get('href', '').lower()
                        if any(blocked in raw_link for blocked in blocked_domains): continue
                            
                        confidence = score_website_match(raw_link, company_name_clean)
                        if confidence > best_score:
                            best_score, best_url = confidence, raw_link
                    
                    if best_score >= 40: found_domain = best_url
            except Exception as e:
                print(f"DDG Website Search failed: {e}")

            # LinkedIn Lookup
            found_linkedin = None
            try:
                strict_query = f'{company_name_strict} UK site:linkedin.com/company/'
                
                with DDGS() as ddgs:
                    # Pull the top 3 results to compare them
                    results = list(ddgs.text(strict_query, max_results=3))
                    
                    best_li_score = 0
                    best_li_url = None
                    
                    for result in results:
                        raw_link = result.get('href', '')
                        title = result.get('title', '')
                        snippet = result.get('body', '')
                        
                        if "/company/" in raw_link and "/jobs/" not in raw_link:
                            # Pass the URL, title, and snippet to our new scoring model
                            confidence = score_linkedin_match(raw_link, title, snippet, company_name_clean)
                            print(f"   [LinkedIn Scanner] {raw_link} -> Confidence: {confidence}/100")
                            
                            if confidence > best_li_score:
                                best_li_score = confidence
                                # Clean off any ugly tracking tags (e.g., ?trk=public_profile)
                                best_li_url = raw_link.split('?')[0] 
                                
                    # Threshold: We only accept the link if the score is 40 or higher
                    if best_li_score >= 40:
                        found_linkedin = best_li_url
                    else:
                        print("   [LinkedIn Scanner] No results passed the confidence threshold.")
            except Exception as e:
                print(f"DDG Search failed: {e}")
                
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

            # ---------------------------------------------------------
            # DATABASE UPDATE
            # ---------------------------------------------------------
            print(f" -> Website: {found_domain} ({web_status})")
            print(f" -> LinkedIn: {found_linkedin} ({li_status})")
            print(f" -> OVERALL SCORE: {combined_score}")
            
            connection.execute(
                update(sales_leads).where(sales_leads.c.id == record.id)
                .values(
                    website_url=found_domain, 
                    linkedin_url=found_linkedin, 
                    confidence_score=combined_score, # Push the score to the DB!
                    status='ready_for_swipe'
                )
            )
            
        print("\nEnrichment batch complete!")

def clear_database():
    print("Connecting to database to initiate wipe...")
    with engine.begin() as connection:
        result = connection.execute(delete(sales_leads))
        print(f"SUCCESS: Wiped {result.rowcount} records from the sales_leads table.")

# ==========================================
# 4. PIPELINE MANAGERS
# ==========================================
def run_sourcing_pipeline():
    print("UI Triggered: Sourcing Pipeline...")
    fetch_and_store_random_batch()
    return "Sourcing complete!"

def run_enrichment_pipeline():
    print("UI Triggered: Enrichment Pipeline...")
    enrich_sourced_leads(limit=100)
    return "Enrichment complete!"

def clear_all_data():
    print("UI Triggered: Wiping database...")
    clear_database()
    return "Database wiped."

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