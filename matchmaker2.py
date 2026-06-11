import random
import requests
import sqlalchemy
import re
import urllib3
from ddgs import DDGS
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from google.cloud.sql.connector import Connector, IPTypes
from sqlalchemy import Table, Column, Integer, String, Date, Boolean, DateTime, MetaData, select, update, delete

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# 1. DATABASE CONNECTION SETUP
# ==========================================
connector = Connector()

def get_db_connection():
    # Replace these with your actual GCP instance details
    instance_connection_name = "enrichmentno:europe-west2:matchmaker-2"
    db_user = "postgres"
    db_password = "sDMAN5)7&R^Xi+%;"
    db_name = "sales-pipeline"

    conn = connector.connect(
        instance_connection_name,
        "pg8000",
        user=db_user,
        password=db_password,
        db=db_name,
        ip_type=IPTypes.PUBLIC  # Use IPTypes.PRIVATE if your script is within the GCP VPC network
    )
    return conn

# Create your SQLAlchemy engine using the connector creator function
engine = sqlalchemy.create_engine(
    "postgresql+pg8000://",
    creator=get_db_connection,
)

# matchmaker2.0.py



# ==========================================
# 2. TABLE SCHEMA DEFINITION
# ==========================================
metadata = MetaData()

sales_leads = Table(
    'sales_leads', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('crn', String(20), unique=True, nullable=False),
    Column('company_name', String(255), nullable=False),
    Column('incorporation_date', Date),
    
    # Companies House Registry Data
    Column('sic_codes', String(255)), 
    
    # Enrichment Fields
    Column('website_url', String(500)),
    Column('linkedin_url', String(500)),
    Column('contact_email', String(255)),
    
    # Accuracy Tracking Flags
    Column('website_accurate', Boolean, default=None),
    Column('linkedin_accurate', Boolean, default=None),
    Column('contact_accurate', Boolean, default=None),
    
    # Pipeline Management
    Column('status', String(50), default='sourced'),
    Column('assigned_ae_username', String(100)),
    Column('created_at', DateTime, default=datetime.utcnow),
    Column('updated_at', DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
)

# Physically creates/verifies the table in Google Cloud SQL
metadata.create_all(engine)
print("Database schema successfully verified on Google Cloud!")


# ==========================================
# 3. WORKER FUNCTIONS (The Heavy Lifters)
# ==========================================

def fetch_and_store_random_batch():
    # Query Companies House Advanced Search
    ch_url = "https://api.company-information.service.gov.uk/advanced-search/companies"
    # !!! PASTE YOUR API KEY HERE !!!
    ch_api_key = "afec6750-bfcc-47e0-86f9-d71e94952574"  

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
            print(" -> 0 active companies found (likely a weekend/holiday). Rolling a new date...\n")
            continue
            
        if response.status_code != 200:
            print(f" -> API Error: {response.status_code}")
            return

        companies_data = response.json().get('items', [])
        print(f" -> Retrieved {len(companies_data)} active records. Committing to Cloud SQL...")

        with engine.begin() as connection:
            inserted_count = 0
            for item in companies_data:
                company_name = item.get('company_name')
                crn = item.get('company_number')
                
                raw_sic_codes = item.get('sic_codes', [])
                formatted_sic_codes = ", ".join(raw_sic_codes) if raw_sic_codes else None
                
                insert_stmt = sales_leads.insert().values(
                    crn=crn,
                    company_name=company_name,
                    incorporation_date=target_date_obj.date(),
                    sic_codes=formatted_sic_codes,
                    status='sourced'
                )
                
                try:
                    connection.execute(insert_stmt)
                    inserted_count += 1
                except sqlalchemy.exc.IntegrityError:
                    continue
                    
            print(f"Successfully loaded {inserted_count} net new rows into sales_leads.")
            
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
            
            if clean_name_lower in title:
                score += 40
            if clean_name_lower in meta_content:
                score += 20
            if clean_name_lower in body_text:
                score += 20
                
            if any(bad in title for bad in ['directory', 'company profile', 'job', 'overview', 'wiki']):
                score -= 50
                
    except Exception:
        pass
        
    return min(score, 100)


def enrich_sourced_leads(limit=None):
    print("Starting enrichment phase...")
    
    # logo_api_key removed as we are fully DDGS now!
    
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
                '', 
                company_name_strict, 
                flags=re.IGNORECASE
            ).strip()
            
            row_id = record.id
            print(f"\nEnriching: {company_name_strict}")
            
            # ---------------------------------------------------------
            # LOOKUP 1: Website Domain (DuckDuckGo + Scoring)
            # ---------------------------------------------------------
            found_domain = None
            try:
                website_query = f'{company_name_clean} UK official website'
                
                with DDGS() as ddgs:
                    results = list(ddgs.text(website_query, max_results=3))
                    
                    best_score = 0
                    best_url = None
                    
                    blocked_domains = [
                        'linkedin.com', 'companieshouse', 'endole.co.uk', 
                        'facebook.com', 'gov.uk', 'instagram.com', 
                        'twitter.com', 'yelp.co.uk', 'yell.com', 'companycheck.co.uk',
                        'sparklane-group', 'theladders.com', 'bloomberg.com',
                        'wikipedia.org', 'crunchbase.com', 'pitchbook.com', 'zoominfo.com',
                        'dunandbradstreet', 'apollo.io', 'glassdoor', 'suite.endole'
                    ]
                    
                    for result in results:
                        raw_link = result.get('href', '')
                        raw_link_lower = raw_link.lower()
                        
                        if any(blocked in raw_link_lower for blocked in blocked_domains):
                            continue
                            
                        confidence = score_website_match(raw_link, company_name_clean)
                        print(f"   [Website Scanner] {raw_link} -> Confidence: {confidence}/100")
                        
                        if confidence > best_score:
                            best_score = confidence
                            best_url = raw_link
                    
                    if best_score >= 40:
                        found_domain = best_url
                    else:
                        print("   [Website Scanner] No results passed the confidence threshold.")
                            
            except Exception as e:
                print(f"DDG Website Search failed: {e}")

            # ---------------------------------------------------------
            # LOOKUP 2: LinkedIn Company Page
            # ---------------------------------------------------------
            found_linkedin = None
            try:
                strict_query = f'{company_name_strict} UK site:linkedin.com/company/'
                
                with DDGS() as ddgs:
                    results = list(ddgs.text(strict_query, max_results=1))
                    
                    if results:
                        raw_link = results[0].get('href', '')
                        if "/company/" in raw_link and "/jobs/" not in raw_link:
                            found_linkedin = raw_link.split('?')[0]
            except Exception as e:
                print(f"DDG Search failed: {e}")

            # ---------------------------------------------------------
            # DATABASE UPDATE
            # ---------------------------------------------------------
            print(f" -> Website: {found_domain}")
            print(f" -> LinkedIn: {found_linkedin}")
            
            update_stmt = update(sales_leads).where(
                sales_leads.c.id == row_id
            ).values(
                website_url=found_domain,
                linkedin_url=found_linkedin,
                status='ready_for_swipe'
            )
            
            connection.execute(update_stmt)
            
        print("\nEnrichment batch complete!")


def clear_database():
    print("Connecting to database to initiate wipe...")
    with engine.begin() as connection:
        delete_stmt = delete(sales_leads)
        result = connection.execute(delete_stmt)
        print(f"SUCCESS: Wiped {result.rowcount} records from the sales_leads table.")


# ==========================================
# 4. PIPELINE MANAGERS (For Streamlit UI)
# ==========================================
# These are the functions Streamlit imports and attaches to buttons.

def run_sourcing_pipeline():
    print("UI Triggered: Sourcing Pipeline...")
    fetch_and_store_random_batch()
    return "Sourcing complete!"

def run_enrichment_pipeline():
    print("UI Triggered: Enrichment Pipeline...")
    enrich_sourced_leads(limit=100) # Default to 100 for UI responsiveness
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
        print("\n" + "="*35)
        print(" MATCHMAKER 2.0 - CONTROL PANEL")
        print("="*35)
        print("1. Source: Pull 100 companies from a random day")
        print("2. Enrich: Process a batch of 100 leads")
        print("3. Enrich: Process ALL un-enriched leads")
        print("4. Clear:  Wipe entire database table")
        print("5. Exit")
        
        choice = input("\nEnter your choice (1-5): ").strip()
        
        if choice == '1':
            fetch_and_store_random_batch()
            
        elif choice == '2':
            enrich_sourced_leads(limit=100)
            
        elif choice == '3':
            confirm = input("WARNING: This will process ALL 'sourced' leads. Continue? (y/n): ").strip().lower()
            if confirm == 'y':
                enrich_sourced_leads(limit=None)
            else:
                print("Aborted.")
                
        elif choice == '4':
            confirm = input("DANGER: Are you sure you want to completely WIPE the sales_leads database? (y/n): ").strip().lower()
            if confirm == 'y':
                clear_database()
            else:
                print("Database clear aborted.")
                
        elif choice == '5':
            print("Shutting down...")
            break
            
        else:
            print("Invalid choice. Please enter 1, 2, 3, 4, or 5.")

# ==========================================
# 6. MAIN EXECUTION
# ==========================================
# This ensures the terminal menu ONLY opens if you run this file directly.
# If Streamlit imports this file, this block gets completely ignored!
if __name__ == "__main__":
    main_menu()