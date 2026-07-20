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
    fetch_filing_triggers,
)
from second_enrichment import second_enrich_lead
from scoring import score_lead, best_possible_score, features_from_mapping
from settings import get_float_setting, get_qualify_bar
from sic_weights import get_sic_multipliers, multiplier_for
from model_scorer import score_lead_model

# A small random fraction of leads bypass the gates and go to AEs regardless of
# score (a "holdout"). This is the one thing that keeps the future training data
# unbiased: without it we'd only ever learn outcomes for leads the filter let
# through, and could never tell whether it was wrongly binning good ones — and it
# is what lets the SIC weighting self-correct (sic_weights.py). This is the code
# default; override at runtime via app_settings key "holdout_rate" (0 disables).
HOLDOUT_RATE = 0.05


def get_holdout_rate():
    return get_float_setting("holdout_rate", HOLDOUT_RATE)


def _screened(fields, reason):
    """Mark a lead eliminated — kept in the DB with the reason, never deleted. A
    screened-out lead is also unassigned: it must not sit in an AE's pile or count
    as 'assigned' to anyone."""
    fields["status"] = "screened_out"
    fields["screen_reason"] = reason
    fields["assigned_ae_username"] = None
    fields["assigned_date"] = None
    print(f" -> SCREENED OUT — {reason}")
    return fields


def screen_lead(record, bar=None, sic_multipliers=None, holdout_rate=None):
    """Run one 'sourced' lead through the staged pipeline. Returns the dict of
    fields to write (including the final status). `bar` defaults to the current
    admin-set qualification bar.

    A random `holdout_rate` fraction of leads are 'holdouts': they skip the
    elimination gates and go through to the AEs whatever they score, so we get
    labelled outcomes for leads the filter would otherwise have binned."""
    if bar is None:
        bar = get_qualify_bar()
    if holdout_rate is None:
        holdout_rate = get_holdout_rate()
    is_holdout = random.random() < holdout_rate
    strict, clean = clean_company_name(record.company_name)
    # The lead's industry multiplier, from how its SIC group has actually
    # converted (sic_weights.py). 1.0 = no history / no opinion. Holdouts are
    # scored with it too — only the GATES below ignore them, so their stored
    # score stays comparable with everyone else's.
    sic_mult = multiplier_for(record.sic_codes, sic_multipliers)
    sic_note = "" if sic_mult == 1.0 else f" [SIC x{sic_mult:.2f}]"
    print(f"\nScreening: {strict}  (bar {bar}){sic_note}{' [HOLDOUT]' if is_holdout else ''}")

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
        "sic_multiplier": sic_mult,
    }
    feats = features_from_mapping(fields)
    fields["lead_score"] = score_lead(feats, sic_mult)
    # Only bin if even a perfect accounts result couldn't reach the bar.
    best = best_possible_score(feats, sic_mult)
    if best < bar and not is_holdout:
        return _screened(fields, f"Stage A · best-case {best} < bar {bar} (dormant / out of segment)")

    # ---- Stage B: costly accounts parsing ----
    accounts = second_enrich_lead(record.crn)
    fields.update(accounts)
    fields["second_enriched"] = True
    fields["lead_score"] = score_lead(features_from_mapping(fields), sic_mult)
    # The accounts figures are known now, so gate on the realistic score.
    if fields["lead_score"] < bar and not is_holdout:
        reason = f"Stage B · fit score {fields['lead_score']} < bar {bar}"
        if sic_mult < 1.0:
            # Name the industry penalty when it's what tipped the lead out, so
            # the effect is auditable rather than an invisible hand.
            unweighted = score_lead(features_from_mapping(fields))
            if unweighted >= bar:
                reason += f" (SIC x{sic_mult:.2f}; would have been {unweighted})"
        return _screened(fields, reason)

    # ---- Stage C: costly website + LinkedIn (qualifiers + holdouts) ----
    fields.update(fetch_web_presence(clean, strict))
    # "Why now" filing triggers (SH01 capital raise / MR01 borrowing) — computed
    # here, not at Stage A, so we only spend the 2 extra CH calls on leads that
    # actually become swipeable.
    fields.update(fetch_filing_triggers(record.crn))
    fields["status"] = "ready_for_swipe"
    # SHADOW MODE: now that the web features exist, record what the trained model
    # would score this lead. Purely observational — it changes no gate and no
    # ordering. None when no model is deployed (model_scorer fails safe).
    fields["model_score"] = score_lead_model(fields)
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
    # One set of industry multipliers for the whole batch too, recomputed from
    # the labelled log on every run — that's what makes the weighting sharpen by
    # itself as more leads get swiped.
    get_sic_multipliers.cache_clear()
    sic_multipliers = get_sic_multipliers()
    holdout_rate = get_holdout_rate()             # ditto — one rate per batch
    print(f"Found {total} leads to screen (qualification bar = {bar}/100)...")
    if sic_multipliers:
        nudged = {g: m for g, m in sic_multipliers.items() if not 0.95 <= m <= 1.05}
        print(f"SIC weighting active on {len(nudged)} of {len(sic_multipliers)} industry groups.")

    done = 0
    for record in records:
        fields = screen_lead(record, bar=bar, sic_multipliers=sic_multipliers,
                             holdout_rate=holdout_rate)
        log_row = {
            "lead_id": record.id,
            "crn": record.crn,
            "company_name": record.company_name,
            "sic_codes": record.sic_codes,
            "incorporation_date": record.incorporation_date,
            "confidence_score": fields.get("confidence_score"),
            "website_score": fields.get("website_score"),
            "linkedin_score": fields.get("linkedin_score"),
            "account_type": fields.get("account_type"),
            "employee_count": fields.get("employee_count"),
            "turnover": fields.get("turnover"),
            "cash_at_bank": fields.get("cash_at_bank"),
            "foreign_exchange": fields.get("foreign_exchange"),
            "trade_debtors": fields.get("trade_debtors"),
            "trade_creditors": fields.get("trade_creditors"),
            "import_activity": fields.get("import_activity"),
            "export_activity": fields.get("export_activity"),
            "director_change_recent": fields.get("director_change_recent"),
            "capital_raise_recent": fields.get("capital_raise_recent"),
            "charge_recent": fields.get("charge_recent"),
            "lead_score": fields.get("lead_score"),
            "model_score": fields.get("model_score"),
            "sic_multiplier": fields.get("sic_multiplier"),
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
