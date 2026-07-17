"""Diagnostic: fetch + parse ONE company's latest accounts document, verbosely.
Handy for checking the iXBRL path on a company that files FULL accounts (which
include the P&L, so turnover / admin expenses should be present).

    python test_accounts.py <CRN>

Find a full-accounts filer in your data with:
    SELECT crn, company_name, account_type FROM sales_leads
    WHERE account_type IN ('full', 'medium', 'group') LIMIT 10;

Read-only — it only fetches from Companies House and parses in memory; it does
NOT write to the database. Needs a project .env (CH_API_KEY) locally.
"""
import logging
import sys

logging.getLogger("streamlit").setLevel(logging.ERROR)

from bs4 import BeautifulSoup  # noqa: E402
from second_enrichment import (  # noqa: E402
    find_accounts_document, download_accounts,
    _parse_ixbrl, _parse_pdf, _ixbrl_value, ACCOUNTS_FIELDS,
)


def _dump_ixbrl_tags(content):
    soup = BeautifulSoup(content, "html.parser")
    named = [t for t in soup.find_all(True) if t.get("name")]
    print(f"\niXBRL: {len(named)} tags carry a `name` attribute")
    namespaces = sorted({(t.get("name") or "").split(":")[0]
                         for t in named if ":" in (t.get("name") or "")})
    print(f"namespaces seen: {namespaces[:15]}")

    print("\nPer-field candidate tags  (name | text | scale | sign | -> parsed):")
    for field, cfg in ACCOUNTS_FIELDS.items():
        matches = [t for t in named
                   if any(c in t.get("name").lower() for c in cfg["concepts"])]
        print(f"\n  [{field}]  concepts={cfg['concepts']}  -> {len(matches)} match(es)")
        for t in matches[:8]:
            print(f"     {t.get('name')} | {t.get_text().strip()[:20]!r} | "
                  f"scale={t.get('scale')} sign={t.get('sign')} -> {_ixbrl_value(t)}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_accounts.py <CRN>")
        return
    crn = sys.argv[1].strip()
    print(f"=== CRN {crn} ===")

    url = find_accounts_document(crn)
    print(f"document_metadata: {url}")
    if not url:
        print("No 'accounts' filing found in the filing history.")
        return

    content, kind = download_accounts(url)
    print(f"downloaded: kind={kind}  bytes={len(content) if content else 0}")
    if not content:
        print("Download failed (see any error above).")
        return

    if kind == "ixbrl":
        _dump_ixbrl_tags(content)
        data = _parse_ixbrl(content)
    else:
        data = _parse_pdf(content)

    print("\n=== Parsed result ===")
    for k, v in data.items():
        print(f"  {k} = {v}")


if __name__ == "__main__":
    main()
