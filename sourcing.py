"""Sourcing: pull fresh companies from the Companies House API into the pool.

`source_leads(count, ...)` is the entry point (used by the Railway lead worker):
it keeps rolling fresh random incorporation dates (one date per ~100-lead batch —
the advanced-search endpoint returns at most 100 per call, so this is the
lightest way to keep the random-date behaviour) until `count` NEW leads are in
the pool or the attempt budget runs out.
"""
import random
import time
from datetime import datetime, timedelta

import requests
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ch_client import get_secret   # .env locally, env var on Railway
from database import engine
from models import sales_leads

CH_SEARCH_URL = "https://api.company-information.service.gov.uk/advanced-search/companies"
BATCH_SIZE = 100                    # advanced-search max page size
DATE_RANGE_DAYS = (365, 7300)       # incorporated between ~1 and ~20 years ago


def _fetch_one_date(target_date_obj):
    """One advanced-search call for one incorporation date. Returns the number
    of NET NEW leads stored (0 if the date had no active companies), or None on
    an API error."""
    response = requests.get(
        CH_SEARCH_URL,
        auth=(get_secret("CH_API_KEY"), ""),
        params={
            "incorporated_from": target_date_obj.strftime("%Y-%m-%d"),
            "incorporated_to": target_date_obj.strftime("%Y-%m-%d"),
            "company_status": "active",
            "company_type": "ltd,llp",
            "size": BATCH_SIZE,
        },
        timeout=15,  # never wait forever on a stuck network call
    )

    if response.status_code == 404:
        return 0                       # no active companies that day — roll again
    if response.status_code != 200:
        print(f" -> API Error: {response.status_code}")
        return None

    batch_data = []
    for item in response.json().get("items", []):
        raw_sic = item.get("sic_codes", [])
        batch_data.append({
            "crn": item.get("company_number"),
            "company_name": item.get("company_name"),
            "incorporation_date": target_date_obj.date(),
            "sic_codes": ", ".join(raw_sic) if raw_sic else None,
            "status": "sourced",
        })

    if not batch_data:
        return 0
    with engine.begin() as connection:
        insert_stmt = pg_insert(sales_leads).values(batch_data)
        # If the CRN already exists, silently skip it rather than crashing.
        result = connection.execute(
            insert_stmt.on_conflict_do_nothing(index_elements=["crn"])
        )
        return result.rowcount


def _random_date():
    return datetime.now() - timedelta(days=random.randint(*DATE_RANGE_DAYS))


def source_leads(count, progress_callback=None, should_stop=None):
    """Source ~`count` net-new leads, one fresh random date per batch of up to
    100. Dates that duplicate existing leads or land on quiet days just roll
    again, within an overall attempt budget so this can never loop forever.

    progress_callback(sourced_so_far, count) is called after each batch.
    should_stop() returning True aborts between batches (job cancellation).
    Returns the number of net new leads stored.
    """
    sourced = 0
    # ~10 date-rolls per needed batch mirrors the one-shot's patience.
    max_attempts = (count // BATCH_SIZE + 1) * 10
    attempts = 0
    while sourced < count and attempts < max_attempts:
        if should_stop and should_stop():
            break
        attempts += 1
        new_rows = _fetch_one_date(_random_date())
        if new_rows is None:
            break                      # hard API error — stop with what we have
        sourced += new_rows
        if progress_callback:
            progress_callback(min(sourced, count), count)
        time.sleep(0.5)                # be polite to the API between calls
    return sourced
