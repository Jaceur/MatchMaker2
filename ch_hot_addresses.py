"""Build the ch_hot_addresses table from the Companies House bulk snapshot.

Formation agents register thousands of companies at the same office; a
registered address shared by ≥100 live companies is a mass-registration
address, and a NewCo registered there loses points (formation_agent_address).
Until this has been run, the hardcoded seed list in ch_signals.py applies —
running this ADDS to that list, it doesn't replace it.

One-time setup (refresh monthly):
 1. Download the free "Basic Company Data" one-file snapshot (CSV, zipped)
    from http://download.companieshouse.gov.uk/en_output.html
 2. Unzip it (~2.5 GB CSV).
 3. python ch_hot_addresses.py BasicCompanyDataAsOneFile-2026-07-01.csv

Streams the CSV row by row; the address counter needs roughly 1-2 GB of RAM
for the ~5.5M live companies.
"""
import csv
import sys
from collections import Counter
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

from ch_signals import normalise_address
from database import engine
from models import ch_hot_addresses

HOT_THRESHOLD = 100      # live companies sharing an address to call it "hot"


def count_addresses(csv_path):
    """Normalised registered-office address -> live-company count."""
    counts = Counter()
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        # CH's CSV headers are notorious for stray leading spaces.
        reader.fieldnames = [h.strip() for h in reader.fieldnames]
        for i, row in enumerate(reader, start=1):
            if row.get("CompanyStatus", "").strip().lower() != "active":
                continue
            key = normalise_address(" ".join(filter(None, (
                row.get("RegAddress.AddressLine1", "").strip(),
                row.get("RegAddress.AddressLine2", "").strip(),
                row.get("RegAddress.PostTown", "").strip(),
                row.get("RegAddress.PostCode", "").strip(),
            ))))
            if key:
                counts[key] += 1
            if i % 500_000 == 0:
                print(f"  ...{i:,} rows read")
    return counts


def refresh_hot_addresses(csv_path, threshold=HOT_THRESHOLD):
    counts = count_addresses(csv_path)
    hot = {addr: n for addr, n in counts.items() if n >= threshold}
    print(f"{len(counts):,} distinct addresses; {len(hot):,} at >= {threshold} companies")

    now = datetime.utcnow()
    rows = [{"address_normalised": a[:500], "company_count": n, "refreshed_at": now}
            for a, n in hot.items()]
    with engine.begin() as conn:
        for start in range(0, len(rows), 1000):
            batch = rows[start:start + 1000]
            stmt = pg_insert(ch_hot_addresses).values(batch)
            conn.execute(stmt.on_conflict_do_update(
                index_elements=["address_normalised"],
                set_={"company_count": stmt.excluded.company_count,
                      "refreshed_at": stmt.excluded.refreshed_at},
            ))
    print(f"ch_hot_addresses refreshed: {len(rows):,} rows upserted.")
    return len(rows)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ch_hot_addresses.py <path-to-BasicCompanyData.csv>")
        sys.exit(1)
    refresh_hot_addresses(sys.argv[1])
