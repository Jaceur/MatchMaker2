"""Enrichment: scrape + score each sourced lead's website and LinkedIn, plus a
couple of Companies House signals (account category, recent director change)."""
import re
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse, urljoin

import requests
from ddgs import DDGS
from bs4 import BeautifulSoup
from rapidfuzz import fuzz  # pip install rapidfuzz

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

# A site that shows the exact registered legal name (incl. LTD/LIMITED) — in its
# footer or privacy/terms page — is almost certainly the real one. This bonus
# alone is enough to clear WEBSITE_MIN_SCORE.
LEGAL_NAME_BONUS = 50

# A LinkedIn link found on the company's OWN website (footer/social icons) is
# self-declared, so we trust it highly — enough to count as a "high" match.
SITE_LINKEDIN_SCORE = 80

# Companies House extras pulled during enrichment.
CH_PROFILE_URL = "https://api.company-information.service.gov.uk/company/{crn}"
CH_FILING_URL = "https://api.company-information.service.gov.uk/company/{crn}/filing-history"
DIRECTOR_CHANGE_FORMS = {"AP01", "TM01"}   # appoint / terminate a person director
DIRECTOR_CHANGE_RECENT_DAYS = 183          # ~6 months

# HMRC UK Trade Info (no API key) — does the company appear as an importer /
# exporter? Direction is structural: separate Imports / Exports navigation sets.
UKTRADE_TRADER_URL = "https://api.uktradeinfo.com/Trader"

# Sites we never want to mistake for a company's own website.
# Defined once here, instead of being rebuilt for every single lead.
BLOCKED_DOMAINS = [
    'linkedin.com', 'companieshouse', 'company-information', 'endole.co.uk', 'facebook.com', 'gov.uk',
    'instagram.com', 'twitter.com', 'yelp.co.uk', 'yell.com', 'companycheck.co.uk',
    'sparklane-group', 'theladders.com', 'bloomberg.com', 'wikipedia.org',
    'crunchbase.com', 'pitchbook.com', 'zoominfo.com', 'dunandbradstreet',
    'apollo.io', 'glassdoor', 'suite.endole', 'globaldatabase',
    'companiesintheuk', 'ukbizdb', 'checkcompany', 'entia.systems'
]


def _normalise_for_match(text):
    """Lowercase, unify Limited/Ltd, and drop punctuation so 'Acme Widgets, Ltd.'
    and 'ACME WIDGETS LIMITED' compare equal."""
    text = text.lower().replace("limited", "ltd")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _legal_page_url(soup, base_url):
    """First privacy / terms / legal link on the page (as an absolute URL), or None."""
    for a in soup.find_all('a', href=True):
        label = a.get_text(strip=True).lower()
        href = a['href'].lower()
        if any(kw in label or kw in href for kw in ('privacy', 'terms', 'legal', 'imprint')):
            return urljoin(base_url, a['href'])
    return None


def _fetch_text(url):
    """Visible text of a page, or '' on any error (cert/timeout/non-200)."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            return BeautifulSoup(resp.text, 'html.parser').get_text(separator=' ', strip=True)
    except Exception:
        pass
    return ""


def score_website_match(url, company_name_clean, company_name_strict=None):
    score = 0
    clean_name_lower = company_name_clean.lower()
    first_word = clean_name_lower.split()[0] if clean_name_lower else ""
    domain = url.split('/')[2].lower() if '//' in url else url.lower()
    domain_label = (domain[4:] if domain.startswith('www.') else domain).split('.')[0]
    clean_no_spaces = clean_name_lower.replace(" ", "")

    # The registrable domain label tells us a lot on its own — enough to accept
    # (and even rate "high") without reading the page, which often can't be read
    # anyway (slow/blocked server, JS-only content, no name in the HTML).
    if clean_no_spaces and domain_label == clean_no_spaces:
        score += 70   # the domain IS the company name (cherubinmusicproductions.co.uk) -> high
    elif clean_no_spaces and domain_label.startswith(clean_no_spaces):
        score += 50   # domain starts with the name (e.g. nameltd.co.uk)
    elif clean_no_spaces and clean_no_spaces in domain:
        score += 30   # name appears somewhere in the domain
    elif first_word and first_word in domain:
        score += 15

    # The full registered name (incl. LTD/LIMITED), normalised for matching.
    strict_needle = _normalise_for_match(company_name_strict) if company_name_strict else ""

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
            full_text = soup.get_text(separator=' ', strip=True).lower()

            if clean_name_lower in title: score += 40
            if clean_name_lower in meta_content: score += 20
            if clean_name_lower in full_text[:5000]: score += 20

            if any(bad in title for bad in ['directory', 'company profile', 'job', 'overview', 'wiki']):
                score -= 50

            # Strong signal: the exact legal name appears on the site. It usually
            # lives in the footer or the privacy/terms page rather than the body
            # copy, so scan the WHOLE page, then fall back to the legal page.
            if strict_needle:
                if strict_needle in _normalise_for_match(full_text):
                    score += LEGAL_NAME_BONUS
                else:
                    legal_url = _legal_page_url(soup, url)
                    if legal_url and strict_needle in _normalise_for_match(_fetch_text(legal_url)):
                        score += LEGAL_NAME_BONUS
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


def fetch_ch_signals(crn):
    """From Companies House: the account category (micro-entity / small / medium
    / full…) from the company profile, and the most recent director appoint
    (AP01) / terminate (TM01) date from filing history, with a 'recent' flag."""
    from ch_client import get_secret  # .env locally, env var on Railway
    auth = (get_secret("CH_API_KEY"), "")
    account_type = None
    last_change = None

    # 1. Account category from the company profile.
    try:
        resp = requests.get(CH_PROFILE_URL.format(crn=crn), auth=auth, timeout=15)
        if resp.status_code == 200:
            account_type = (resp.json().get("accounts") or {}).get("last_accounts", {}).get("type")
    except Exception as e:
        print(f"CH profile failed for {crn}: {e}")

    # 2. Most recent director change from the officers filing history.
    try:
        resp = requests.get(
            CH_FILING_URL.format(crn=crn), auth=auth,
            params={"category": "officers", "items_per_page": 100}, timeout=15,
        )
        if resp.status_code == 200:
            dates = []
            for item in resp.json().get("items", []):
                if item.get("type") in DIRECTOR_CHANGE_FORMS and item.get("date"):
                    try:
                        dates.append(datetime.strptime(item["date"], "%Y-%m-%d").date())
                    except ValueError:
                        pass
            if dates:
                last_change = max(dates)
    except Exception as e:
        print(f"CH filing-history failed for {crn}: {e}")

    recent = bool(
        last_change
        and last_change >= datetime.now().date() - timedelta(days=DIRECTOR_CHANGE_RECENT_DAYS)
    )
    return {
        "account_type": account_type,
        "last_director_change": last_change,
        "director_change_recent": recent,
    }


def fetch_trade_activity(company_name):
    """Check HMRC UK Trade Info for any import/export activity under a company
    name. Returns {'imports': bool, 'exports': bool}.

    HMRC trader names are UPPERCASE. We query with an OData contains() on the
    cleaned name, then keep only traders whose CompanyName matches that name on
    word boundaries — so a short needle like 'SUN' doesn't count 'SAMSUNG' as
    trade activity. A non-empty Imports/Exports array on a matched trader means
    activity in that direction. (If the API returns no CompanyName we fall back
    to trusting the contains() filter rather than dropping the row.)
    """
    name = (company_name or "").strip().upper()
    if not name:
        return {"imports": False, "exports": False}
    escaped = name.replace("'", "''")   # OData string-literal escaping
    try:
        resp = requests.get(
            UKTRADE_TRADER_URL,
            params={
                "$filter": f"contains(CompanyName,'{escaped}')",
                "$expand": "Imports($top=1),Exports($top=1)",
                "$top": 10,
            },
            timeout=15,
        )
    except Exception as e:
        print(f"UK Trade Info request failed for {name}: {e}")
        return {"imports": False, "exports": False}

    if resp.status_code != 200:
        print(f"UK Trade Info error for {name}: {resp.status_code}")
        return {"imports": False, "exports": False}

    # A 200 with a non-JSON body would otherwise raise here and abort the lead.
    try:
        traders = resp.json().get("value", [])
    except Exception as e:
        print(f"UK Trade Info bad response for {name}: {e}")
        return {"imports": False, "exports": False}

    name_pattern = re.compile(rf"\b{re.escape(name)}\b")
    imports = exports = False
    matched = 0
    for trader in traders:
        cname = (trader.get("CompanyName") or "").upper()
        # Drop OData substring false-positives (e.g. 'SUN' inside 'SAMSUNG'). If
        # the API didn't return a name, trust the filter rather than over-reject.
        if cname and not name_pattern.search(cname):
            continue
        matched += 1
        if trader.get("Imports"):
            imports = True
        if trader.get("Exports"):
            exports = True
        if imports and exports:
            break
    # Console debug (visible when running enrich_local) — is Trade Info matching?
    print(f" -> Trade Info: '{name}' — {len(traders)} returned, {matched} matched, "
          f"import={imports} export={exports}")
    return {"imports": imports, "exports": exports}


def _extract_linkedin_from_site(url):
    """Find a linkedin.com/company/ link on the company's own website (usually a
    footer/header social icon). Returns the cleaned URL, or None."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if 'linkedin.com/company/' in href.lower():
                    href = href.split('?')[0].rstrip('/')
                    if href.startswith('//'):
                        href = 'https:' + href
                    return href
    except Exception:
        pass
    return None


def clean_company_name(raw):
    """Return (strict, clean): strict = whitespace-normalised name; clean = strict
    with common legal / structural suffix words (LTD, GROUP, UK, ...) removed."""
    strict = re.sub(r'\s+', ' ', raw).strip()
    clean = re.sub(
        r'\b(LTD|LIMITED|LLP|PLC|UK|HOLDINGS|GROUP|ENTERPRISES|SERVICES)\b',
        '', strict, flags=re.IGNORECASE,
    ).strip()
    return strict, clean


# DuckDuckGo has no API key and throttles automated searches, so the web step
# paces itself and retries instead of just failing. Tune these if it's still
# timing out a lot (raise DDG_PACE) or too slow (lower it).
DDG_RETRIES = 2        # extra attempts after a failure
DDG_BACKOFF = 5        # base seconds between retries (grows each attempt)
DDG_PACE = 1.0         # gentle delay before each search to stay under the rate limit


def _ddg_text(ddgs, query, max_results):
    """ddgs.text() with pacing + retry/backoff. DuckDuckGo drops connections under
    a fast batch of searches, so we space requests out and, on a failure, pause
    and retry before giving up (returning [] — the lead just gets no web link)."""
    time.sleep(DDG_PACE)
    for attempt in range(DDG_RETRIES + 1):
        try:
            return list(ddgs.text(query, max_results=max_results))
        except Exception as e:
            if attempt < DDG_RETRIES:
                wait = DDG_BACKOFF * (attempt + 1)
                print(f"   DDG search throttled ({e}); waiting {wait}s, retrying...")
                time.sleep(wait)
            else:
                print(f"   DDG search gave up after {DDG_RETRIES + 1} tries: {e}")
                return []


def fetch_web_presence(company_name_clean, company_name_strict):
    """Search the web for the company's own website and LinkedIn /company/ page,
    score each, and combine them into one 0-100 confidence number. Returns the
    URLs (when confident enough) plus the raw scores. This is the SLOW step, so
    the staged pipeline runs it LAST — only on leads that already qualify on fit."""
    # Safe defaults: if a search blows up these still exist and nothing crashes.
    found_domain = None
    best_score = 0
    found_linkedin = None
    best_li_score = 0
    best_li_title = None
    best_li_snippet = None
    website_candidates = []
    linkedin_candidates = []

    # One search session handles both lookups for this lead.
    try:
        with DDGS() as ddgs:
            # --- Website lookup ---
            # Keep the top 5 scored candidates (not just the winner) so an AE can
            # pick the right one from a dropdown and we learn which they chose.
            try:
                results = _ddg_text(ddgs, f'{company_name_clean} UK official website', max_results=8)
                cands = []
                seen = set()
                for result in results:
                    raw_link = (result.get('href') or '').strip()
                    low = raw_link.lower()
                    if not raw_link or low in seen or any(b in low for b in BLOCKED_DOMAINS):
                        continue
                    seen.add(low)
                    confidence = score_website_match(low, company_name_clean, company_name_strict)
                    cands.append({"url": raw_link, "title": result.get('title', ''), "score": confidence})
                cands.sort(key=lambda c: c["score"], reverse=True)
                website_candidates = cands[:5]
                if website_candidates:
                    best_score = website_candidates[0]["score"]
                    if best_score >= WEBSITE_MIN_SCORE:
                        found_domain = website_candidates[0]["url"]
            except Exception as e:
                print(f"DDG Website Search failed: {e}")

            # --- LinkedIn lookup ---
            try:
                strict_query = f'{company_name_strict} UK site:linkedin.com/company/'
                # LinkedIn /company/ pages are sparsely indexed by DDG, so pull a
                # deeper result set — the scorer still gates quality below.
                results = _ddg_text(ddgs, strict_query, max_results=8)
                cands = []
                seen = set()
                for result in results:
                    raw_link = result.get('href', '')
                    title = result.get('title', '')
                    snippet = result.get('body', '')
                    if "/company/" in raw_link and "/jobs/" not in raw_link:
                        clean_url = raw_link.split('?')[0]
                        if clean_url in seen:
                            continue
                        seen.add(clean_url)
                        confidence = score_linkedin_match(raw_link, title, snippet, company_name_clean)
                        cands.append({"url": clean_url, "title": title, "snippet": snippet, "score": confidence})
                cands.sort(key=lambda c: c["score"], reverse=True)
                if cands:
                    top = cands[0]
                    best_li_score = top["score"]
                    best_li_title = top["title"]
                    best_li_snippet = top["snippet"]
                    if best_li_score >= 40:
                        found_linkedin = top["url"]
                # Store without the bulky snippet (kept above for the columns).
                linkedin_candidates = [
                    {"url": c["url"], "title": c["title"], "score": c["score"]} for c in cands[:5]
                ]
            except Exception as e:
                print(f"DDG LinkedIn Search failed: {e}")
    except Exception as e:
        print(f"Could not start search session: {e}")

    # --- LinkedIn fallback: the company's own website usually links it.
    # DDG indexes /company/ pages poorly, so when we have a website, harvest the
    # LinkedIn straight from it (self-declared = reliable) unless the search
    # already found an equally strong match.
    if found_domain and best_li_score < SITE_LINKEDIN_SCORE:
        site_li = _extract_linkedin_from_site(found_domain)
        if site_li:
            found_linkedin = site_li
            best_li_score = SITE_LINKEDIN_SCORE
            best_li_title = None
            best_li_snippet = None
            # Self-declared on the site = reliable, so surface it as the top pick.
            linkedin_candidates = (
                [{"url": site_li, "title": "(from the company website)", "score": SITE_LINKEDIN_SCORE}]
                + [c for c in linkedin_candidates if c["url"] != site_li]
            )[:5]

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
    print(f" -> CONFIDENCE: {combined_score}")
    print(f" -> Website candidates ({len(website_candidates)}): "
          + ", ".join(f"{c['url']}[{c['score']}]" for c in website_candidates))
    print(f" -> LinkedIn candidates ({len(linkedin_candidates)}): "
          + ", ".join(f"{c['url']}[{c['score']}]" for c in linkedin_candidates))
    return {
        "website_url": found_domain,
        "linkedin_url": found_linkedin,
        "linkedin_raw_title": best_li_title,
        "linkedin_raw_snippet": best_li_snippet,
        "website_score": best_score,
        "linkedin_score": best_li_score,
        "confidence_score": combined_score,
        "website_candidates": website_candidates,
        "linkedin_candidates": linkedin_candidates,
    }


# NOTE: the old single-pass enrichment (enrich_one_lead / enrich_sourced_leads /
# run_enrichment_pipeline) was removed once the staged pipeline in pipeline.py
# became the single enrichment path for every entry point (admin button, CLI,
# enrich_local). The building-block functions above — clean_company_name,
# fetch_web_presence, fetch_ch_signals, fetch_trade_activity — live on and are
# imported by pipeline.py.
