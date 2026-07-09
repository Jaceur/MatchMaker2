"""Enrichment for the CH Lead Engine: drain the ch_queue, pull each company's
profile / PSC / officers / filings from the REST API, detect signals, score,
and persist everything.

Designed to run as a cron loop (ch_run_local.py every ~15 min) or in small
batches from the admin controls on the High Quality New Incorps page. Every
writer upserts on natural keys, so re-running is always safe — streams
redeliver and enrichment retries.

Rate budget: ~4 base REST calls per company (profile, PSC, officers, filing
history) plus up to DIRECTOR_LOOKUP_CAP appointment lookups and
QUALITY_PROFILE_CAP prior-company profiles. At ~3,000 incorporations/day this
sits comfortably inside the 600-per-5-min limit when spread across the day.
"""
from datetime import datetime, timedelta

from sqlalchemy import select, delete, update, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

import ch_client
import ch_signals
import ch_scoring
from database import engine
from models import (
    ch_companies, ch_psc, ch_officers, ch_capital_statements, ch_events,
    ch_scores, ch_queue, ch_hot_addresses,
)

# PSC data can lag incorporation by days. For a company younger than this with
# an empty PSC list we requeue instead of finalising the score...
PSC_LAG_MAX_AGE_DAYS = 14
PSC_RECHECK_HOURS = 48
# ...but never retry forever: after this many attempts, score with what we have.
MAX_ATTEMPTS = 5

# Serial-director quality caps, protecting the rate budget (§4.3):
DIRECTOR_LOOKUP_CAP = 5    # appointments calls per company
QUALITY_PROFILE_CAP = 3    # prior-company profile fetches per company


# ---------------------------------------------------------------------------
# Ingest helper (shared by the stream listener and the backfill)
# ---------------------------------------------------------------------------

def queue_company(conn, company_number, name=None, date_of_creation=None):
    """Idempotently register a company and put it on the enrichment queue.
    Returns True if it was genuinely new (the stream's dedupe check)."""
    stmt = pg_insert(ch_companies).values(
        company_number=company_number,
        name=name,
        date_of_creation=date_of_creation,
        first_seen_at=datetime.utcnow(),
    ).on_conflict_do_nothing(index_elements=["company_number"])
    was_new = conn.execute(stmt).rowcount > 0

    conn.execute(
        pg_insert(ch_queue).values(
            company_number=company_number, stage="new",
            attempts=0, updated_at=datetime.utcnow(),
        ).on_conflict_do_nothing(index_elements=["company_number"])
    )
    return was_new


def load_hot_addresses(conn):
    """Formation-agent addresses: the hardcoded seed list merged with whatever
    the bulk-snapshot build has put in ch_hot_addresses."""
    rows = conn.execute(select(ch_hot_addresses.c.address_normalised)).fetchall()
    return set(ch_signals.SEED_HOT_ADDRESSES) | {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Serial-director quality (the network-using orchestration; the pure pattern
# tests live in ch_signals)
# ---------------------------------------------------------------------------

def assess_directors(company_number, officer_items):
    """Look at up to DIRECTOR_LOOKUP_CAP active directors' prior appointment
    histories. Returns (any_quality, any_spv_farm, per_officer) where
    per_officer maps officer_id -> (prior_count, flag)."""
    any_quality = any_spv = False
    per_officer = {}
    profile_budget = QUALITY_PROFILE_CAP
    today = datetime.utcnow().date()

    for officer in officer_items[:DIRECTOR_LOOKUP_CAP]:
        officer_id = ch_signals.officer_id_from_item(officer)
        if not officer_id:
            continue
        try:
            appts = ch_client.officer_appointments(officer_id)
        except Exception:
            continue   # a flaky lookup must never sink the whole company
        prior = ch_signals.prior_appointments(appts, company_number)
        flag = "unknown"

        if ch_signals.is_spv_farm(prior):
            flag = "spv_farm"
            any_spv = True
        elif not any_quality and profile_budget > 0:
            # Check the most promising prior companies: active ones first,
            # newest appointment first. One qualifying company is enough.
            candidates = sorted(
                (a for a in prior
                 if (a.get("appointed_to") or {}).get("company_status") == "active"
                 and (a.get("appointed_to") or {}).get("company_number")),
                key=lambda a: a.get("appointed_on") or "", reverse=True,
            )
            for appt in candidates[:profile_budget]:
                profile_budget -= 1
                try:
                    prior_profile = ch_client.company_profile(
                        appt["appointed_to"]["company_number"]
                    )
                except Exception:
                    continue
                if ch_signals.is_quality_company(prior_profile, today):
                    flag = "quality"
                    any_quality = True
                    break

        per_officer[officer_id] = (len(prior), flag)
    return any_quality, any_spv, per_officer


# ---------------------------------------------------------------------------
# Scoring persistence (also used by the filings listener to apply events)
# ---------------------------------------------------------------------------

def persist_score(conn, company_number, score, tier, breakdown):
    stmt = pg_insert(ch_scores).values(
        company_number=company_number, score=score, tier=tier,
        breakdown=breakdown, scored_at=datetime.utcnow(),
    ).on_conflict_do_update(
        index_elements=["company_number"],
        set_={"score": score, "tier": tier, "breakdown": breakdown,
              "scored_at": datetime.utcnow()},
    )
    conn.execute(stmt)


def _apply_events_txn(conn, company_number, new_events):
    """Insert each (event_type, detail) row, then re-score the company ONCE with
    the event bonus (promoting it to Tier 1). Callers must have already filtered
    to genuinely new events. Safe if the company isn't scored yet — the events
    still land in ch_events and enrich_company folds them in when it scores.
    """
    for event_type, detail in new_events:
        conn.execute(ch_events.insert().values(
            company_number=company_number, event_type=event_type,
            detail=detail, occurred_at=datetime.utcnow(),
        ))

    row = conn.execute(
        select(ch_scores.c.breakdown)
        .where(ch_scores.c.company_number == company_number)
    ).first()
    if not row:
        return  # not scored yet; enrich_company will include these events
    breakdown = dict(row.breakdown or {})
    if breakdown.get("disqualified"):
        return  # dissolved/odd-type companies stay disqualified

    events = list(breakdown.get("events") or []) + [t for t, _ in new_events]
    # Recompute from the stored per-signal points so the bonus is applied
    # exactly once however many events arrive.
    base = sum(v for k, v in breakdown.items()
               if isinstance(v, int) and k != "event_bonus")
    breakdown["event_bonus"] = ch_scoring.WEIGHTS["event_bonus"]
    breakdown["events"] = events
    persist_score(conn, company_number,
                  base + ch_scoring.WEIGHTS["event_bonus"], 1, breakdown)


# ---------------------------------------------------------------------------
# Event triggers via REST (the default path)
# ---------------------------------------------------------------------------
# Instead of a second long-lived /filings stream, we detect SH01 "fresh raise"
# and MR01 "charge" events with plain REST calls, so the ONLY streaming process
# is the /companies listener (new incorporations). SH01s come from the capital
# filing history; MR01s from the charges endpoint. Each detected event carries a
# stable `ref`, so re-sweeping the same company never double-records.

SH01_MIN_FIGURE = 25000       # a "fresh raise": allot >= £25k (or any non-GBP)
EVENT_MAX_AGE_MONTHS = 18     # only watch companies this young for events


def _detect_events_rest(company_number):
    """[(event_type, detail)] for one company from REST (2 calls: capital
    filing history + charges). Each detail has a de-dup `ref`."""
    detected = []

    filings = ch_client.filing_history(company_number, category="capital")
    for item in (filings or {}).get("items", []) or []:
        ftype = (item.get("type") or "").upper()
        description = item.get("description") or ""
        if ftype == "SH01" or "capital-allotment" in description:
            capital = ch_signals._capital_entries(item.get("description_values"))
            qualifying = [
                c for c in capital
                if (c["currency"] and c["currency"] != "GBP")
                or c["figure"] >= SH01_MIN_FIGURE
            ]
            # An SH01 with no parseable capital still counts: they chose to
            # allot new shares, which is the trigger we care about.
            if qualifying or not capital:
                detected.append(("sh01_raise", {
                    "flag": "fresh_raise", "source": "rest",
                    "ref": item.get("transaction_id") or f"sh01:{item.get('date')}",
                    "description": description,
                    "capital": [{"currency": c["currency"], "figure": str(c["figure"])}
                                for c in capital],
                    "date": item.get("date"),
                }))

    charges = ch_client.charges(company_number)
    for item in (charges or {}).get("items", []) or []:
        detected.append(("mr01_charge", {
            "flag": "debt_financing", "source": "rest",
            "ref": str(item.get("charge_number") or item.get("id")
                       or item.get("etag")),
            "status": item.get("status"),
            "created_on": item.get("created_on"),
            "classification": (item.get("classification") or {}).get("description"),
        }))
    return detected


def detect_events_rest(company_number):
    """Detect SH01/MR01 events for one company via REST and apply only the NEW
    ones (de-dup by `ref`). Returns how many were newly applied."""
    detected = _detect_events_rest(company_number)
    if not detected:
        return 0
    with engine.begin() as conn:
        existing = {
            r[0] for r in conn.execute(
                select(ch_events.c.detail["ref"].astext)
                .where(ch_events.c.company_number == company_number)
            ).fetchall()
        }
        new = [(t, d) for (t, d) in detected if str(d.get("ref")) not in existing]
        if not new:
            return 0
        _apply_events_txn(conn, company_number, new)
    return len(new)


def sweep_events(limit=300, progress_callback=None):
    """REST replacement for the /filings stream: re-check tracked companies
    (<= 18 months old, already scored) for new SH01/MR01 events, youngest first.
    Run on a schedule (ch_run_local.py calls it after draining the queue). Each
    company costs 2 REST calls, so `limit` bounds the rate budget; pass None to
    check every eligible company."""
    cutoff = datetime.utcnow().date() - timedelta(days=EVENT_MAX_AGE_MONTHS * 31)
    with engine.connect() as conn:
        rows = conn.execute(
            select(ch_companies.c.company_number)
            .select_from(ch_companies.join(
                ch_scores,
                ch_companies.c.company_number == ch_scores.c.company_number))
            .where(ch_companies.c.enriched_at.isnot(None))
            .where((ch_companies.c.date_of_creation.is_(None))
                   | (ch_companies.c.date_of_creation >= cutoff))
            .order_by(ch_companies.c.date_of_creation.desc().nullslast())
            .limit(limit)
        ).fetchall()

    applied = 0
    total = len(rows)
    for i, (company_number,) in enumerate(rows, start=1):
        try:
            applied += detect_events_rest(company_number)
        except Exception:
            pass   # a flaky company must never sink the whole sweep
        if progress_callback:
            progress_callback(i, total, company_number)
    return applied


# ---------------------------------------------------------------------------
# The per-company enrichment
# ---------------------------------------------------------------------------

def _replace_children(conn, table, company_number, rows):
    """Idempotent child-table write: wipe the company's rows, insert fresh."""
    conn.execute(delete(table).where(table.c.company_number == company_number))
    if rows:
        conn.execute(table.insert(), rows)


def _company_age_days(profile):
    created = profile.get("date_of_creation")
    if not created:
        return None
    try:
        return (datetime.utcnow().date()
                - datetime.strptime(created, "%Y-%m-%d").date()).days
    except ValueError:
        return None


def enrich_company(conn, company_number, hot_addresses, attempts=0):
    """Fetch, persist and score one company. Returns 'scored' or 'recheck'.
    Raises on hard failures (caller records them on the queue row)."""
    profile = ch_client.company_profile(company_number)
    if profile is None:
        raise RuntimeError("profile not found (404)")

    address = profile.get("registered_office_address") or {}
    address_norm = ch_signals.normalise_address(address)
    sic_list = profile.get("sic_codes") or []

    conn.execute(
        pg_insert(ch_companies).values(
            company_number=company_number,
            name=profile.get("company_name"),
            date_of_creation=profile.get("date_of_creation"),
            status=profile.get("company_status"),
            type=profile.get("type"),
            sic_codes=", ".join(sic_list) if sic_list else None,
            registered_address=address,
            address_normalised=address_norm,
        ).on_conflict_do_update(
            index_elements=["company_number"],
            set_={"name": profile.get("company_name"),
                  "date_of_creation": profile.get("date_of_creation"),
                  "status": profile.get("company_status"),
                  "type": profile.get("type"),
                  "sic_codes": ", ".join(sic_list) if sic_list else None,
                  "registered_address": address,
                  "address_normalised": address_norm},
        )
    )

    # Disqualifiers first — no point spending API calls on a dead company.
    disqualified = None
    if profile.get("company_status") != "active":
        disqualified = f"status: {profile.get('company_status')}"
    elif profile.get("type") not in ch_signals.SUPPORTED_TYPES:
        disqualified = f"type: {profile.get('type')}"
    if disqualified:
        score, tier, breakdown = ch_scoring.score_company({"disqualified": disqualified})
        persist_score(conn, company_number, score, tier, breakdown)
        conn.execute(update(ch_companies)
                     .where(ch_companies.c.company_number == company_number)
                     .values(enriched_at=datetime.utcnow(), recheck_after=None))
        return "scored"

    # PSC — with the lag recheck: a young company with no PSC data yet gets
    # requeued rather than scored incomplete.
    psc_json = ch_client.persons_with_significant_control(company_number)
    psc_items = (psc_json or {}).get("items", []) or []
    age_days = _company_age_days(profile)
    if (not psc_items and age_days is not None
            and age_days < PSC_LAG_MAX_AGE_DAYS and attempts < MAX_ATTEMPTS):
        conn.execute(update(ch_companies)
                     .where(ch_companies.c.company_number == company_number)
                     .values(recheck_after=datetime.utcnow()
                             + timedelta(hours=PSC_RECHECK_HOURS)))
        return "recheck"

    officers_json = ch_client.officers(company_number)
    # One unfiltered filing-history call covers both the incorporation bundle
    # (NEWINC capital via associated_filings) and any capital filings since —
    # a brand-new company's history fits in one page.
    filings_json = ch_client.filing_history(company_number)
    capital_rows = ch_signals.extract_capital_statements(filings_json)

    directors = ch_signals.active_directors(officers_json)
    any_quality, any_spv, per_officer = assess_directors(company_number, directors)

    # --- persist the child tables (idempotent replace) --------------------
    _replace_children(conn, ch_psc, company_number, [
        {"company_number": company_number, "kind": p.get("kind"),
         "name": p.get("name"), "country": ch_signals.psc_country(p), "raw": p}
        for p in psc_items
    ])
    _replace_children(conn, ch_officers, company_number, [
        {"company_number": company_number,
         "officer_id": ch_signals.officer_id_from_item(o),
         "name": o.get("name"), "role": o.get("officer_role"),
         "correspondence_country": (o.get("address") or {}).get("country"),
         "prior_appointments":
             per_officer.get(ch_signals.officer_id_from_item(o), (None, None))[0],
         "quality_flag":
             per_officer.get(ch_signals.officer_id_from_item(o), (None, "unknown"))[1],
         "raw": o}
        for o in (officers_json or {}).get("items", []) or []
    ])
    _replace_children(conn, ch_capital_statements, company_number, [
        {"company_number": company_number, "filing_type": c["filing_type"],
         "currency": c["currency"], "figure": c["figure"],
         "filing_date": c["filing_date"]}
        for c in capital_rows
    ])

    # --- signals → score ---------------------------------------------------
    foreign_psc, uk_psc = ch_signals.psc_signals(psc_json)
    best_gbp, has_foreign_ccy = ch_signals.summarise_capital(capital_rows)
    target_sic, passive_only = ch_signals.sic_flags(sic_list)

    signals = {
        "foreign_corporate_psc": foreign_psc,
        "uk_corporate_psc": uk_psc,
        "best_gbp_capital": best_gbp,
        "has_foreign_currency_capital": has_foreign_ccy,
        "target_sic": target_sic,
        "passive_sic_only": passive_only,
        "active_director_count": len(directors),
        "officer_foreign_address":
            ch_signals.has_foreign_correspondence_officer(officers_json),
        "quality_serial_director": any_quality,
        "spv_farm_director": any_spv,
        "formation_agent_address": address_norm in hot_addresses,
    }

    # Any trigger events that arrived before we got around to scoring.
    event_rows = conn.execute(
        select(ch_events.c.event_type)
        .where(ch_events.c.company_number == company_number)
    ).fetchall()

    score, tier, breakdown = ch_scoring.score_company(
        signals, event_types=[r[0] for r in event_rows]
    )
    persist_score(conn, company_number, score, tier, breakdown)
    conn.execute(update(ch_companies)
                 .where(ch_companies.c.company_number == company_number)
                 .values(enriched_at=datetime.utcnow(), recheck_after=None))
    return "scored"


# ---------------------------------------------------------------------------
# Queue draining (the cron entrypoint's engine)
# ---------------------------------------------------------------------------

def pending_count():
    with engine.connect() as conn:
        return conn.execute(
            select(func.count())
            .select_from(ch_queue)
            .where(ch_queue.c.stage == "new")
        ).scalar() or 0


def drain_queue(limit=None, progress_callback=None):
    """Process queued companies (stage='new', past any recheck_after).
    Returns {'scored': n, 'recheck': n, 'failed': n}."""
    now = datetime.utcnow()
    with engine.connect() as conn:
        rows = conn.execute(
            select(ch_queue.c.company_number, ch_queue.c.attempts)
            .select_from(ch_queue.outerjoin(
                ch_companies,
                ch_queue.c.company_number == ch_companies.c.company_number))
            .where(ch_queue.c.stage == "new")
            .where((ch_companies.c.recheck_after.is_(None))
                   | (ch_companies.c.recheck_after <= now))
            .order_by(ch_queue.c.updated_at)
            .limit(limit)
        ).fetchall()
        hot = load_hot_addresses(conn)

    counts = {"scored": 0, "recheck": 0, "failed": 0}
    total = len(rows)
    for i, (company_number, attempts) in enumerate(rows, start=1):
        try:
            with engine.begin() as conn:
                outcome = enrich_company(conn, company_number, hot,
                                         attempts=attempts or 0)
                if outcome == "scored":
                    stage, err = "scored", None
                else:                       # recheck: stays 'new' for later
                    stage, err = "new", None
                conn.execute(
                    update(ch_queue)
                    .where(ch_queue.c.company_number == company_number)
                    .values(stage=stage, attempts=(attempts or 0) + 1,
                            last_error=err, updated_at=datetime.utcnow())
                )
            counts[outcome] += 1
        except Exception as e:
            counts["failed"] += 1
            with engine.begin() as conn:
                # Give a flaky company MAX_ATTEMPTS tries before parking it.
                new_attempts = (attempts or 0) + 1
                conn.execute(
                    update(ch_queue)
                    .where(ch_queue.c.company_number == company_number)
                    .values(stage="failed" if new_attempts >= MAX_ATTEMPTS else "new",
                            attempts=new_attempts,
                            last_error=str(e)[:500],
                            updated_at=datetime.utcnow())
                )
        if progress_callback:
            progress_callback(i, total, company_number)
    return counts
