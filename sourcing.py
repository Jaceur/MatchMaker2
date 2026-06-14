"""Sourcing: pull fresh companies from the Companies House API into the pool."""
import random
import time
from datetime import datetime, timedelta

import requests
import streamlit as st
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import engine
from models import sales_leads


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


def run_sourcing_pipeline():
    print("UI Triggered: Sourcing Pipeline...")
    new_rows = fetch_and_store_random_batch()
    return f"Sourcing complete! {new_rows} new leads added."
