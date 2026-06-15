"""Enrichment: scrape + score each sourced lead's website and LinkedIn."""
import re
from urllib.parse import urlparse

import requests
from ddgs import DDGS
from bs4 import BeautifulSoup
from rapidfuzz import fuzz  # pip install rapidfuzz
from sqlalchemy import select, update

from database import engine
from models import sales_leads

# Legal/structural words that add no identifying signal when matching a company
# name against a LinkedIn slug.
LEGAL_SUFFIXES = {
    "ltd", "limited", "plc", "llp", "inc", "incorporated", "corp",
    "corporation", "co", "company", "group", "holdings", "uk", "gmbh",
}

# Non-UK entity types. Every lead is a UK Companies House Ltd, so a LinkedIn slug
# ending in one of these is usually the wrong (foreign) entity. We DOCK it rather
# than disqualify it: a high-confidence match to a global brand can still clear
# the bar (its UK Ltd may be that brand's subsidiary).
FOREIGN_LEGAL_FORMS = {"inc", "llc", "gmbh", "ag", "corp", "sarl", "bv", "pty", "srl"}
FOREIGN_SLUG_PENALTY = 30

# A website must score at least this to be auto-accepted. Higher = stricter
# (prefer "Not Found" over a plausible-but-wrong domain). Tune up toward 60 if
# too many wrong sites slip through.
WEBSITE_MIN_SCORE = 50

# Sites we never want to mistake for a company's own website.
# Defined once here, instead of being rebuilt for every single lead.
BLOCKED_DOMAINS = [
    'linkedin.com', 'companieshouse', 'endole.co.uk', 'facebook.com', 'gov.uk',
    'instagram.com', 'twitter.com', 'yelp.co.uk', 'yell.com', 'companycheck.co.uk',
    'sparklane-group', 'theladders.com', 'bloomberg.com', 'wikipedia.org',
    'crunchbase.com', 'pitchbook.com', 'zoominfo.com', 'dunandbradstreet',
    'apollo.io', 'glassdoor', 'suite.endole', 'globaldatabase'
]


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


def normalise_name(name: str) -> str:
    name = name.lower().replace("&", "and")
    name = re.sub(r"[^a-z0-9\s]", " ", name)          # drop punctuation/accents-handling upstream
    tokens = [t for t in name.split() if t and t not in LEGAL_SUFFIXES]
    return " ".join(tokens)


def extract_company_slug(url: str) -> str:
    parsed = urlparse(url)                             # strips query + fragment
    if "linkedin.com" not in parsed.netloc:
        return ""
    parts = [p for p in parsed.path.split("/") if p]
    if "company" not in parts:
        return ""
    idx = parts.index("company")
    return parts[idx + 1].lower() if idx + 1 < len(parts) else ""


def contains_word(text: str, phrase: str) -> bool:
    return bool(re.search(rf"\b{re.escape(phrase)}\b", text, re.IGNORECASE))


def score_linkedin_match(url, title, snippet, company_name_clean):
    slug = extract_company_slug(url)
    if not slug:                                       # not a /company/ URL -> disqualify
        return 0

    name = normalise_name(company_name_clean)          # e.g. "revolut ltd" -> "revolut"
    name_compact = name.replace(" ", "")
    slug_compact = slug.replace("-", "")
    title = (title or "")
    snippet = (snippet or "")

    score = 0

    # SIGNAL 1: slug similarity (fuzzy, threshold-gated)
    if name_compact == slug_compact:
        score += 40
    else:
        ratio = fuzz.ratio(name_compact, slug_compact)
        if ratio >= 90:
            score += 30
        elif ratio >= 80:
            score += 15
        # below 80 contributes nothing — no more "starts with 'the'" matches

    # SIGNAL 2: corroboration (word-boundary, not substring)
    if contains_word(title, name):
        score += 30
    if contains_word(snippet, name):
        score += 20

    # SIGNAL 3: geography (looser boundaries)
    if re.search(r"\b(uk|united kingdom|london|england)\b", snippet, re.IGNORECASE):
        score += 10

    # PENALTIES
    if any(bad in url.lower() for bad in ("/showcase/", "/school/", "/pulse/", "/directory/")):
        score -= 50

    # Non-UK entity type in the slug (e.g. rpa-engineering-inc) — soft penalty so
    # a strong global-brand match survives but a weak foreign one is filtered out.
    if set(slug.split("-")) & FOREIGN_LEGAL_FORMS:
        score -= FOREIGN_SLUG_PENALTY

    return min(max(score, 0), 100)


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
                if best_score >= WEBSITE_MIN_SCORE:
                    found_domain = best_url
            except Exception as e:
                print(f"DDG Website Search failed: {e}")

            # --- LinkedIn lookup ---
            try:
                strict_query = f'{company_name_strict} UK site:linkedin.com/company/'
                # LinkedIn /company/ pages are sparsely indexed by DDG, so pull a
                # deeper result set — the scorer still gates quality below.
                results = list(ddgs.text(strict_query, max_results=8))
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
    web_status = "high" if best_score >= 70 else "low" if best_score >= WEBSITE_MIN_SCORE else "none"
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


def enrich_sourced_leads(limit=None, progress_callback=None):
    """Enrich every 'sourced' lead, saving each as it goes.

    progress_callback, if given, is called once per lead as
    progress_callback(done, total, company_name) — `done` leads finished out of
    `total`, plus the name of the one just completed — so a UI can drive a live
    progress bar.
    """
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

    total = len(records_to_enrich)
    print(f"Found {total} leads to enrich...")

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
        if progress_callback:
            progress_callback(enriched_count, total, record.company_name)

    print("\nEnrichment batch complete!")
    return enriched_count


def run_enrichment_pipeline(progress_callback=None):
    print("UI Triggered: Enrichment Pipeline...")
    count = enrich_sourced_leads(limit=100, progress_callback=progress_callback)
    return f"Enrichment complete! {count} leads processed."
