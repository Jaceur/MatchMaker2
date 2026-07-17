"""SIC-group score weighting — nudge a lead's fit score by how its industry
actually converts, damped by how much evidence we have.

WHY
---
Approval rate varies hugely by industry: on the labelled data, Restaurants/Pubs
convert at ~6% while Software/Data converts at ~81%, against a ~34% baseline.
The rules in scoring.py are blind to that. This module turns the observed history
into a multiplier the scorer applies to its output.

THE SAMPLE-SIZE PROBLEM
-----------------------
A group with 3 approvals from 10 leads looks like "30%", but the honest read is
"we have no idea" — at n=10 the 95% range spans roughly 2%-58%, which straddles
the baseline. Acting on that would be superstition. So:

    below MIN_GROUP_N leads      -> multiplier exactly 1.0, no opinion
    otherwise:
        weight w  = n / (n + K)          K = SHRINK_K
        effective = w * group_rate + (1 - w) * baseline
        multiplier = effective / baseline        (clamped)

As n grows the group's own rate takes over from the baseline. K is the "prior
strength": the sample size at which a group is believed half as much as the
baseline.

Note the multiplier is driven by the DISTANCE from the baseline, not just the
weight — so a group sitting near the baseline (Construction, 33% vs 34%) lands at
~1.0 whatever its n. The weight only protects against small-sample noise.

THIS EVOLVES. It is recomputed from the log on every pipeline run, so as leads
get swiped the weights sharpen on their own. That self-correction depends on the
5% holdout continuing to feed data for penalised groups — see pipeline.HOLDOUT_RATE.

WHY THE DURABLE LOG, NOT sales_leads
------------------------------------
Rates come from screening_log ⋈ ml_pipeline_analytics, NOT the live pool.
`leads.clear_database()` deletes every lead that isn't 'approved' — i.e. it wipes
PASSES and keeps APPROVALS — so the live pool's approval rate climbs every time
someone clears it (51% there vs 34% here). Scoring off that would bake in the
bias and silently shift the algorithm whenever an admin pressed a button.
"""
import functools

import pandas as pd
from sqlalchemy import text

from database import engine

# Sample size at which a group's own rate is believed as much as the baseline.
# 25 keeps small groups near-neutral while letting a genuinely strong n=21 signal
# (Software/Data) through at w=0.46.
SHRINK_K = 25

# Below this many decided leads, a group gets NO adjustment at all — flat 1.0.
# Shrinkage alone isn't enough of a guard at the very bottom: a group at 100%
# from 4 leads still earns w=0.14, and 0.14 x the huge distance from a 34%
# baseline came out as a 1.27x boost. The binomial interval is the honest test —
# at n=10 a 10% rate spans roughly 0%-45%, which straddles the baseline (no
# signal), while by n=15 it spans ~1%-30% and genuinely clears it. So 15 is where
# "we have no idea" ends.
MIN_GROUP_N = 15

# How far the multiplier may travel. Caps a runaway group (and stops an
# unlucky/small group being zeroed out entirely) — the "not completely" rule:
# a strong lead in a weak industry can still clear the bar on its other signals.
MULT_MIN, MULT_MAX = 0.5, 1.5

# Below this many decided leads overall, don't weight anything: the baseline
# itself would be too noisy to shrink toward.
MIN_TOTAL = 100

_RATES_SQL = text("""
    SELECT s.sic_codes, v.approved
    FROM (
        SELECT DISTINCT ON (lead_id) lead_id, sic_codes
        FROM screening_log
        WHERE lead_id IS NOT NULL
        ORDER BY lead_id, created_at DESC
    ) s
    JOIN (
        SELECT lead_id, BOOL_OR(is_worth_it) AS approved
        FROM ml_pipeline_analytics
        WHERE lead_id IS NOT NULL AND is_worth_it IS NOT NULL
        GROUP BY lead_id
    ) v ON v.lead_id = s.lead_id
""")


def shrink_multiplier(group_rate, n, baseline, k=SHRINK_K):
    """The multiplier for one group. Pure — the maths, with no database."""
    if not baseline or n < MIN_GROUP_N:
        return 1.0
    w = n / (n + k)
    effective = w * group_rate + (1 - w) * baseline
    return max(MULT_MIN, min(MULT_MAX, effective / baseline))


def compute_sic_multipliers():
    """{group -> multiplier} plus the stats behind them, straight from the log.

    Returns (multipliers, info) where info holds baseline/total and the per-group
    rate + n, so callers can log or display WHY a lead was nudged."""
    from sic_data import get_sic_records

    with engine.connect() as conn:
        df = pd.read_sql(_RATES_SQL, conn)

    records = get_sic_records()
    sections = {code: rec["section"] for code, rec in records.items()}

    info = {"baseline": None, "total": 0, "groups": {}}
    if df.empty:
        return {}, info

    df["approved"] = df["approved"].astype(bool).astype(int)
    # The lead's PRIMARY (first-listed) SIC code decides its group, matching the
    # analytics board. No zero-padding: CH already sends 5-digit codes (see
    # sic_data.parse_sic_codes).
    df["group"] = (
        df["sic_codes"].fillna("").astype(str)
        .str.split(",").str[0].str.strip().map(sections)
    )
    df = df[df["group"].notna() & (df["group"] != "")]
    if df.empty:
        return {}, info

    baseline = float(df["approved"].mean())
    total = int(len(df))
    info["baseline"], info["total"] = baseline, total
    if total < MIN_TOTAL:
        return {}, info

    multipliers = {}
    for group, g in df.groupby("group"):
        rate, n = float(g["approved"].mean()), int(len(g))
        mult = shrink_multiplier(rate, n, baseline)
        multipliers[group] = mult
        info["groups"][group] = {"rate": rate, "n": n, "multiplier": mult}
    return multipliers, info


@functools.lru_cache(maxsize=1)
def get_sic_multipliers():
    """Cached {group -> multiplier}. Cheap enough to recompute per pipeline run;
    cached so scoring a batch of leads doesn't re-query per lead. Call
    `get_sic_multipliers.cache_clear()` to force a refresh."""
    try:
        multipliers, _ = compute_sic_multipliers()
        return multipliers
    except Exception as e:
        # Never let a weighting problem stop the pipeline — fall back to "no
        # adjustment", which is exactly the old behaviour.
        print(f"SIC weighting unavailable ({e}) — scoring without it.")
        return {}


def multiplier_for(sic_codes, multipliers=None):
    """The multiplier for a lead's `sic_codes` string (its primary code's group).
    1.0 when the group is unknown or has no history — i.e. no opinion."""
    from sic_data import get_sic_records, parse_sic_codes

    if multipliers is None:
        multipliers = get_sic_multipliers()
    codes = parse_sic_codes(sic_codes)
    if not codes:
        return 1.0
    rec = get_sic_records().get(codes[0])
    if not rec or not rec.get("section"):
        return 1.0
    return multipliers.get(rec["section"], 1.0)


if __name__ == "__main__":
    mults, info = compute_sic_multipliers()
    if not info["baseline"]:
        raise SystemExit("No labelled leads yet.")
    print(f"Baseline approval {info['baseline']:.0%} over {info['total']} decided leads "
          f"(shrink K={SHRINK_K}, clamp {MULT_MIN}-{MULT_MAX})\n")
    print(f"{'group':<34} {'rate':>5} {'n':>4} {'weight':>7} {'mult':>6}")
    print("-" * 60)
    for group, s in sorted(info["groups"].items(), key=lambda kv: kv[1]["multiplier"]):
        w = s["n"] / (s["n"] + SHRINK_K)
        flag = "" if 0.95 <= s["multiplier"] <= 1.05 else "  <-"
        print(f"{group[:34]:<34} {s['rate']:>4.0%} {s['n']:>4} {w:>6.0%} {s['multiplier']:>6.2f}{flag}")
