"""Second enrichment — pull figures from a company's filed accounts document.

For each Tier 3 lead we: find the most recent 'accounts' (AA) filing in the
Companies House filing history, follow its document_metadata to the Document
API, download the iXBRL (preferred) or PDF, and extract fields.

Today we extract employee count. To add a field later, add ONE entry to
ACCOUNTS_FIELDS — `concepts` are matched against iXBRL tag names, `pattern` is a
regex run over PDF text. Nothing else changes.

This runs locally (it uses Tesseract/poppler for scanned PDFs), not on the
Streamlit app — see second_enrich_local.py.
"""
import io
import re
import time

import requests
import streamlit as st
from bs4 import BeautifulSoup
from sqlalchemy import select, update

from database import engine
from models import sales_leads
from leads import TIER_THRESHOLD

CH_FILING_URL = "https://api.company-information.service.gov.uk/company/{crn}/filing-history"

# ----------------------------------------------------------------------------
# Field map — the single place to extend what we pull from accounts.
#   concepts: substrings matched (case-insensitive) against iXBRL tag `name`
#             attributes, e.g. uk-core:AverageNumberEmployeesDuringPeriod.
#   pattern:  regex with one capture group, run over extracted PDF text.
# ----------------------------------------------------------------------------
ACCOUNTS_FIELDS = {
    # Per field: `concepts` = substrings matched against iXBRL tag `name`
    # attributes; `labels` = regex anchors searched in PDF/OCR text; `signed` =
    # keep the sign (brackets / 'loss' -> negative). employee_count is a head
    # count handled specially; the rest are monetary amounts (stored positive
    # except the signed ones).
    "employee_count":        {"concepts": ["averagenumberemployees"]},
    "turnover":              {"concepts": ["turnoverrevenue", "turnover"],
                              "labels": [r"\bturnover\b", r"\brevenue\b"]},
    "cash_at_bank":          {"concepts": ["cashbankinhand", "cashandcashequivalents", "cashatbank"],
                              "labels": [r"cash at bank and in hand", r"cash at bank",
                                         r"cash and cash equivalents"]},
    "foreign_exchange":      {"concepts": ["foreignexchangegainslosses", "foreignexchange"],
                              "labels": [r"foreign exchange", r"exchange differences", r"\bfx\b"],
                              "signed": True},
    "trade_debtors":         {"concepts": ["tradedebtors"],
                              "labels": [r"trade debtors"]},
    "trade_creditors":       {"concepts": ["tradecreditors"],
                              "labels": [r"trade creditors"]},
    "admin_expenses":        {"concepts": ["administrativeexpenses"],
                              "labels": [r"administrative expenses", r"administration costs"]},
    "bank_loans_overdrafts": {"concepts": ["bankloansoverdrafts", "bankloans"],
                              "labels": [r"bank loans and overdrafts", r"bank loans", r"bank overdrafts"]},
}

# Spelled-out small numbers — accounts for tiny companies often write
# "...amounted to two" / "was nil" instead of a digit.
_WORD_NUMBERS = {
    "nil": 0, "none": 0, "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}


# ==========================================
# COMPANIES HOUSE / DOCUMENT API
# ==========================================
def _ch_get(url, headers=None, params=None, timeout=20):
    """GET with the CH API key, pausing once on a 429 rate-limit. Returns the
    response (or None on a hard failure)."""
    for _attempt in range(2):
        try:
            resp = requests.get(
                url, auth=(st.secrets["CH_API_KEY"], ""),
                headers=headers or {}, params=params, timeout=timeout,
            )
        except Exception as e:
            print(f"   CH request failed ({url[:60]}…): {e}")
            return None
        if resp.status_code == 429:
            print("   Companies House rate limit (429) — pausing 60s…")
            time.sleep(60)
            continue
        return resp
    return None


def find_accounts_document(crn):
    """document_metadata URL of the most recent 'accounts' filing, or None."""
    resp = _ch_get(
        CH_FILING_URL.format(crn=crn),
        params={"category": "accounts", "items_per_page": 100},
    )
    if not resp or resp.status_code != 200:
        return None
    for item in resp.json().get("items", []):   # most-recent-first
        if item.get("category") == "accounts":
            link = (item.get("links") or {}).get("document_metadata")
            if link:
                return link
    return None


def download_accounts(metadata_url):
    """Download the accounts document. Returns (content_bytes, 'ixbrl'|'pdf') or
    (None, None). Prefers iXBRL; falls back to PDF."""
    resp = _ch_get(metadata_url)
    if not resp or resp.status_code != 200:
        return None, None
    meta = resp.json()
    resources = meta.get("resources", {}) or {}
    content_url = (meta.get("links") or {}).get("document") or metadata_url.rstrip("/") + "/content"

    if "application/xhtml+xml" in resources:
        doc = _ch_get(content_url, headers={"Accept": "application/xhtml+xml"}, timeout=30)
        if doc and doc.status_code == 200 and doc.content:
            return doc.content, "ixbrl"
    if "application/pdf" in resources:
        doc = _ch_get(content_url, headers={"Accept": "application/pdf"}, timeout=60)
        if doc and doc.status_code == 200 and doc.content:
            return doc.content, "pdf"
    return None, None


# ==========================================
# PARSERS
# ==========================================
def _ixbrl_value(tag):
    """Numeric value of an iXBRL fact, honouring its `scale` (power of ten) and
    `sign` attributes. 'nil' -> 0; non-numeric -> None."""
    raw = tag.get_text() or ""
    if not re.search(r"\d", raw):
        return 0 if "nil" in raw.lower() else None
    val = int(re.sub(r"[^\d]", "", raw))
    scale = tag.get("scale")
    if scale:
        try:
            val *= 10 ** int(scale)
        except ValueError:
            pass
    if (tag.get("sign") or "").strip() == "-":
        val = -val
    return val


def _parse_ixbrl(content):
    """Pull fields from an iXBRL document by matching tag `name` attributes. The
    first match (usually the current period) wins; magnitudes are stored positive
    except for the explicitly-signed fields (foreign_exchange)."""
    soup = BeautifulSoup(content, "html.parser")
    data = {field: None for field in ACCOUNTS_FIELDS}
    for tag in soup.find_all(True):
        nm = tag.get("name")
        if not nm:
            continue
        nm_lower = nm.lower()
        for field, cfg in ACCOUNTS_FIELDS.items():
            if data[field] is not None:
                continue
            if any(c in nm_lower for c in cfg["concepts"]):
                val = _ixbrl_value(tag)
                if val is not None:
                    data[field] = val if cfg.get("signed") else abs(val)
    return data


def _pdf_text(content):
    """Text from a PDF: the embedded text layer (pdfplumber) if present, else
    OCR (pdf2image + pytesseract). Imports are lazy so the module loads without
    the OCR stack installed."""
    text = ""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception as e:
        print(f"   pdfplumber failed: {e}")

    if len(text.strip()) >= 100:
        return text

    # Scanned/flat PDF -> OCR.
    try:
        import pdf2image
        import pytesseract
        images = pdf2image.convert_from_bytes(content)
        text = "\n".join(pytesseract.image_to_string(img) for img in images)
        print(f"   OCR'd {len(images)} page(s)")
    except Exception as e:
        print(f"   OCR failed (need poppler + tesseract installed): {e}")
    return text


def _employee_count_from_text(text):
    """Find the employee count in extracted PDF/OCR text. Scans EVERY
    'number of … employ…' mention (the first 'employ' is often a narrative aside),
    then takes the first non-year number after it — falling back to a spelled-out
    small number ('amounted to two', 'was nil'). Prints what it matched."""
    first_ctx = None
    for m in re.finditer(r"employ\w+", text):
        # Must be the 'average number of … employees/employed' note, not a
        # narrative mention like "the company's employees".
        if "number of" not in text[max(0, m.start() - 45): m.start()]:
            continue
        ctx = text[max(0, m.start() - 25): m.end() + 100]
        if first_ctx is None:
            first_ctx = ctx
        tail = text[m.end(): m.end() + 120]
        # The count sits right after 'was'/'amounted to', BEFORE any stray
        # figures further in the note (e.g. 'nil (2024: nil) … notes … 31'). So
        # take the EARLIEST value by position — a non-year digit or a spelled-out
        # number, whichever comes first.
        pos = val = None
        for dm in re.finditer(r"\d[\d,]*", tail):
            n = int(dm.group().replace(",", ""))
            if 1990 <= n <= 2035:               # year-column header, not a count
                continue
            pos, val = dm.start(), n
            break
        wm = re.search(r"\b(" + "|".join(_WORD_NUMBERS) + r")\b", tail)
        if wm and (pos is None or wm.start() < pos):
            val = _WORD_NUMBERS[wm.group(1)]
        if val is not None:
            print(f"   [emp] …{ctx}… -> {val}")
            return val
    if first_ctx:
        print(f"   [emp] note found but no number nearby: …{first_ctx}…")
    else:
        print("   [emp] no 'number of … employ' note found in extracted text")
    return None


_MONEY_RE = re.compile(r"(\(?)\s*£?\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(\)?)")


def _money_after_label(text, labels, signed=False, in_thousands=False):
    """A labelled monetary amount in PDF/OCR text. Takes the left-most (current-
    year) number after the label, understands 'nil' (-> 0), strips brackets and
    £ symbols, applies £'000 scaling, and — for signed fields — goes negative on
    brackets or a nearby 'loss'."""
    for label in labels:
        for lm in re.finditer(label, text):
            window = text[lm.end(): lm.end() + 60]
            if re.match(r"[^0-9]{0,12}\bnil\b", window):     # 'NIL' -> 0
                return 0
            nm = _MONEY_RE.search(window)
            if not nm or not nm.group(2):
                continue
            val = int(nm.group(2).replace(",", ""))
            if in_thousands:
                val *= 1000
            if signed:
                near = text[max(0, lm.start() - 15): lm.end() + 30]
                if nm.group(1) or nm.group(3) or "loss" in near:
                    val = -val
            return val
    return None


def _parse_pdf(content):
    text = re.sub(r"\s+", " ", _pdf_text(content)).lower()
    # Scale check: if the statements are presented in £'000, multiply figures by
    # 1000. (Document-level heuristic — most accounts use one scale throughout.)
    in_thousands = bool(re.search(r"£\s*'?\s*000|in thousands", text))
    data = {field: None for field in ACCOUNTS_FIELDS}
    data["employee_count"] = _employee_count_from_text(text)
    for field, cfg in ACCOUNTS_FIELDS.items():
        if "labels" not in cfg:
            continue
        data[field] = _money_after_label(text, cfg["labels"], cfg.get("signed", False), in_thousands)
    return data


# ==========================================
# ORCHESTRATION
# ==========================================
def second_enrich_lead(crn):
    """Return the accounts-derived fields for one company (keys always present,
    None when not found)."""
    result = {field: None for field in ACCOUNTS_FIELDS}
    metadata_url = find_accounts_document(crn)
    if not metadata_url:
        return result
    content, kind = download_accounts(metadata_url)
    if not content:
        print("   no accounts document content")
        return result
    print(f"   parsing {kind} ({len(content)} bytes)")
    parsed = _parse_ixbrl(content) if kind == "ixbrl" else _parse_pdf(content)
    result.update({k: v for k, v in parsed.items() if v is not None})
    return result


def second_enrich_tier3(limit=None, progress_callback=None):
    """Process Tier 3 leads (confidence above the threshold, already first-
    enriched) that haven't been second-enriched yet. Returns how many were done."""
    with engine.connect() as conn:
        query = select(sales_leads).where(
            (sales_leads.c.status != 'sourced')
            & (sales_leads.c.confidence_score > TIER_THRESHOLD)
            & (sales_leads.c.second_enriched.isnot(True))
        )
        if limit is not None:
            query = query.limit(limit)
        records = conn.execute(query).mappings().fetchall()

    total = len(records)
    if not records:
        print("No Tier 3 leads need second enrichment.")
        return 0

    print(f"Second-enriching {total} Tier 3 lead(s)...")
    done = 0
    for rec in records:
        print(f"\n[{done + 1}/{total}] {rec['company_name']} ({rec['crn']})")
        data = second_enrich_lead(rec['crn'])
        with engine.begin() as conn:
            conn.execute(
                update(sales_leads)
                .where(sales_leads.c.id == rec['id'])
                .values(second_enriched=True, **data)
            )
        done += 1
        if progress_callback:
            progress_callback(done, total, rec['company_name'], data)
    return done
