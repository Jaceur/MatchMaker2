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
import random
from datetime import datetime

from sqlalchemy import select, update, insert

from database import engine
from models import sales_leads, screening_log
from enrichment import (
    clean_company_name, fetch_ch_signals, fetch_trade_activity, fetch_web_presence,
)
from second_enrichment import second_enrich_lead
from scoring import score_lead, best_possible_score, features_from_mapping
from settings import get_qualify_bar

# A small random fraction of leads bypass the gates and go to AEs regardless of
# score (a "holdout"). This is the one thing that keeps the future training data
# unbiased: without it we'd only ever learn outcomes for leads the filter let
# through, and could never tell whether it was wrongly binning good ones. Set to
# 0 to switch it off.
HOLDOUT_RATE = 0.05


def _screened(fields, reason):
    """Mark a lead eliminated — kept in the DB with the reason, never deleted."""
    fields["status"] = "screened_out"
    fields["screen_reason"] = reason
    print(f" -> SCREENED OUT — {reason}")
    return fields


def screen_lead(record, bar=None):
    """Run one 'sourced' lead through the staged pipeline. Returns the dict of
    fields to write (including the final status). `bar` defaults to the current
    admin-set qualification bar.

    A random HOLDOUT_RATE fraction of leads are 'holdouts': they skip the
    elimination gates and go through to the AEs whatever they score, so we get
    labelled outcomes for leads the filter would otherwise have binned."""
    if bar is None:
        bar = get_qualify_bar()
    is_holdout = random.random() < HOLDOUT_RATE
    strict, clean = clean_company_name(record.company_name)
    print(f"\nScreening: {strict}  (bar {bar}){' [HOLDOUT]' if is_holdout else ''}")

    # ---- Stage A: cheap Companies House + HMRC trade ----
    ch = fetch_ch_signals(record.crn)
    trade = fetch_trade_activity(clean)
    fields = {
        "account_type": ch["account_type"],
        "last_director_change": ch["last_director_change"],
        "director_change_recent": ch["director_change_recent"],
        "import_activity": trade["imports"],
        "export_activity": trade["exports"],
        "is_holdout": is_holdout,
    }
    feats = features_from_mapping(fields)
    fields["lead_score"] = score_lead(feats)
    # Only bin if even a perfect accounts result couldn't reach the bar.
    best = best_possible_score(feats)
    if best < bar and not is_holdout:
        return _screened(fields, f"Stage A · best-case {best} < bar {bar} (dormant / out of segment)")

    # ---- Stage B: costly accounts parsing ----
    accounts = second_enrich_lead(record.crn)
    fields.update(accounts)
    fields["second_enriched"] = True
    fields["lead_score"] = score_lead(features_from_mapping(fields))
    # The accounts figures are known now, so gate on the realistic score.
    if fields["lead_score"] < bar and not is_holdout:
        return _screened(fields, f"Stage B · fit score {fields['lead_score']} < bar {bar}")

    # ---- Stage C: costly website + LinkedIn (qualifiers + holdouts) ----
    fields.update(fetch_web_presence(clean, strict))
    fields["status"] = "ready_for_swipe"
    if is_holdout and fields["lead_score"] < bar:
        fields["screen_reason"] = f"HOLDOUT — score {fields['lead_score']} < bar {bar}, kept for training"
        print(f" -> HOLDOUT kept (lead_score {fields['lead_score']} < bar {bar})")
    else:
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
        log_row = {
            "lead_id": record.id,
            "crn": record.crn,
            "company_name": record.company_name,
            "account_type": fields.get("account_type"),
            "employee_count": fields.get("employee_count"),
            "turnover": fields.get("turnover"),
            "cash_at_bank": fields.get("cash_at_bank"),
            "foreign_exchange": fields.get("foreign_exchange"),
            "import_activity": fields.get("import_activity"),
            "export_activity": fields.get("export_activity"),
            "director_change_recent": fields.get("director_change_recent"),
            "lead_score": fields.get("lead_score"),
            "qualify_bar": bar,
            "qualified": fields.get("status") == "ready_for_swipe",
            "is_holdout": fields.get("is_holdout", False),
            "screen_reason": fields.get("screen_reason"),
            "created_at": datetime.utcnow(),
        }
        with engine.begin() as connection:
            connection.execute(
                update(sales_leads).where(sales_leads.c.id == record.id).values(**fields)
            )
            connection.execute(insert(screening_log).values(**log_row))
        done += 1
        if progress_callback:
            progress_callback(done, total, record.company_name)

    print("\nStaged pipeline complete!")
    return done


def run_enrichment_pipeline(progress_callback=None, limit=25):
    """Entry point for the admin 'Run Enrichment' button — runs the staged
    pipeline on a small batch (capped low, since the full pipeline incl. accounts
    parsing is slow per lead; run big batches locally via enrich_local.py).
    Returns a summary string for the UI."""
    count = run_pipeline(limit=limit, progress_callback=progress_callback)
    return f"Pipeline complete — {count} leads screened/enriched."
