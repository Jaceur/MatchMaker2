"""The staged lead pipeline: screen each 'sourced' lead through cheap-to-costly
stages, eliminating it as soon as it can't reach the qualification bar, so the
slow steps only ever run on survivors.

    Stage A  (cheap)   Companies House account category + recent director change,
                       HMRC import/export.        -> provisional fit score
    Stage B  (costly)  filed-accounts figures (employees, turnover, cash, FX).
                       -> fuller fit score
    Stage C  (costly)  website + LinkedIn search.  -> confidence_score

"Start safe": a lead is binned only when even its BEST possible score (if every
not-yet-seen figure turned out perfectly) still can't reach the bar — so we never
drop a good lead early just because its strong signals come late. The bar is read
from the admin slider via settings.get_qualify_bar(). Eliminated leads are kept
with status='screened_out' and a screen_reason; they are never deleted.

This replaces the old first-then-second enrichment split for new leads. Run it
locally via enrich_local.py (Stage B needs the accounts parser / OCR).
"""
from sqlalchemy import select, update

from database import engine
from models import sales_leads
from enrichment import (
    clean_company_name, fetch_ch_signals, fetch_trade_activity, fetch_web_presence,
)
from second_enrichment import second_enrich_lead
from scoring import score_lead, best_possible_score, features_from_mapping
from settings import get_qualify_bar


def _screened(fields, reason):
    """Mark a lead eliminated — kept in the DB with the reason, never deleted."""
    fields["status"] = "screened_out"
    fields["screen_reason"] = reason
    print(f" -> SCREENED OUT — {reason}")
    return fields


def screen_lead(record, bar=None):
    """Run one 'sourced' lead through the staged pipeline. Returns the dict of
    fields to write (including the final status). `bar` defaults to the current
    admin-set qualification bar."""
    if bar is None:
        bar = get_qualify_bar()
    strict, clean = clean_company_name(record.company_name)
    print(f"\nScreening: {strict}  (bar {bar})")

    # ---- Stage A: cheap Companies House + HMRC trade ----
    ch = fetch_ch_signals(record.crn)
    trade = fetch_trade_activity(clean)
    fields = {
        "account_type": ch["account_type"],
        "last_director_change": ch["last_director_change"],
        "director_change_recent": ch["director_change_recent"],
        "import_activity": trade["imports"],
        "export_activity": trade["exports"],
    }
    feats = features_from_mapping(fields)
    fields["lead_score"] = score_lead(feats)
    # Only bin if even a perfect accounts result couldn't reach the bar.
    best = best_possible_score(feats)
    if best < bar:
        return _screened(fields, f"Stage A · best-case {best} < bar {bar} (dormant / out of segment)")

    # ---- Stage B: costly accounts parsing ----
    accounts = second_enrich_lead(record.crn)
    fields.update(accounts)
    fields["second_enriched"] = True
    fields["lead_score"] = score_lead(features_from_mapping(fields))
    # The accounts figures are known now, so gate on the realistic score.
    if fields["lead_score"] < bar:
        return _screened(fields, f"Stage B · fit score {fields['lead_score']} < bar {bar}")

    # ---- Stage C: costly website + LinkedIn (only for leads that qualify) ----
    fields.update(fetch_web_presence(clean, strict))
    fields["status"] = "ready_for_swipe"
    fields["screen_reason"] = None
    print(f" -> QUALIFIED (lead_score {fields['lead_score']} >= bar {bar})")
    return fields


def run_pipeline(limit=None, progress_callback=None):
    """Screen + enrich every 'sourced' lead through the staged pipeline, saving
    each as it goes (so a crash at lead 80 keeps leads 1-79). Returns how many
    were processed.

    progress_callback, if given, is called once per lead as
    progress_callback(done, total, company_name)."""
    print("Starting staged pipeline...")
    with engine.connect() as connection:
        query = select(sales_leads).where(sales_leads.c.status == 'sourced')
        if limit is not None:
            query = query.limit(limit)
        records = connection.execute(query).fetchall()

    if not records:
        print("No new leads to screen.")
        return 0

    total = len(records)
    bar = get_qualify_bar()                       # one bar for the whole batch
    print(f"Found {total} leads to screen (qualification bar = {bar}/100)...")

    done = 0
    for record in records:
        fields = screen_lead(record, bar=bar)
        with engine.begin() as connection:
            connection.execute(
                update(sales_leads).where(sales_leads.c.id == record.id).values(**fields)
            )
        done += 1
        if progress_callback:
            progress_callback(done, total, record.company_name)

    print("\nStaged pipeline complete!")
    return done
