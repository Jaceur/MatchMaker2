"""High Quality New Incorps — the CH Lead Engine's page.

Shows newly incorporated UK companies scored for expected banking usage
(FX flows, real capital, cross-border structure), tiered:
  Tier 1 (score ≥ 60)  — high-touch outbound
  Tier 2 (30–59)       — automated sequence
  Tier 3 (< 30)        — stored but hidden by default

Everyone can browse, filter and drill into the signal breakdown, then either
**Pass** a lead (hidden from their own board only) or **Add to pipe** (claims it
first-come-first-served — removed from everyone else's board — and surfaces the
company's contact details with copy buttons in "My pipe"). Admins also get the
pipeline controls (backfill, enrich, digests).
"""
import html
import re

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from sqlalchemy import text

CH_LINK = "https://find-and-search.company-information.service.gov.uk/company/"

# Plain-English labels for the breakdown drilldown (falls back to the raw key).
SIGNAL_LABELS = {
    "foreign_corporate_psc": "Foreign corporate parent (PSC)",
    "foreign_currency_capital": "Share capital in a foreign currency",
    "capital_50k_plus": "Paid-up capital ≥ £50k",
    "capital_10k_to_50k": "Paid-up capital £10k–£50k",
    "target_sic": "Target sector (wholesale / ecommerce / logistics / software)",
    "quality_serial_director": "Director previously ran a real company",
    "two_plus_directors": "Two or more directors",
    "officer_foreign_address": "Director based abroad",
    "uk_corporate_psc": "UK corporate parent (group subsidiary)",
    "formation_agent_address": "Registered at a formation-agent address",
    "single_director_low_capital": "Single director with ≤ £100 capital",
    "passive_sic_only": "Holding company / property SPV codes only",
    "spv_farm_director": "Director with a trail of dead SPVs",
    "event_bonus": "Trigger event (fresh raise / new charge)",
}


# ==========================================
# CACHED READS
# ==========================================
@st.cache_data(ttl=60)
def _get_stats(_engine):
    query = text("""
        SELECT
            (SELECT COUNT(*) FROM ch_scores WHERE tier = 1) AS tier1,
            (SELECT COUNT(*) FROM ch_scores WHERE tier = 2) AS tier2,
            (SELECT COUNT(*) FROM ch_scores)                AS scored,
            (SELECT COUNT(*) FROM ch_queue WHERE stage = 'new')    AS queued,
            (SELECT COUNT(*) FROM ch_queue WHERE stage = 'failed') AS failed,
            (SELECT COUNT(*) FROM ch_events
              WHERE occurred_at > now() - interval '7 days')       AS events_7d
    """)
    with _engine.connect() as conn:
        row = conn.execute(query).mappings().fetchone()
    return dict(row) if row else {}


@st.cache_data(ttl=60)
def _get_companies(_engine, tiers, sic_filter, name_filter, events_only, username):
    # Excludes: suppressed (GDPR), ALREADY CLAIMED by anyone (taken — it's a
    # race), and anything THIS user has passed (per-user, so a pass doesn't rob
    # teammates). username is part of the cache key, so each rep sees their
    # own board.
    query = """
        SELECT c.company_number, c.name, c.date_of_creation, c.sic_codes,
               s.score, s.tier, s.breakdown, s.scored_at,
               EXISTS (SELECT 1 FROM ch_events e
                       WHERE e.company_number = c.company_number) AS has_event
        FROM ch_scores s
        JOIN ch_companies c ON c.company_number = s.company_number
        WHERE s.tier = ANY(:tiers)
          AND c.company_number NOT IN (SELECT company_number FROM ch_suppression)
          AND c.company_number NOT IN (SELECT company_number FROM ch_claims)
          AND c.company_number NOT IN (
                SELECT company_number FROM ch_passes WHERE username = :user)
    """
    params = {"tiers": tiers, "user": username}
    if sic_filter:
        query += " AND c.sic_codes ILIKE :sic"
        params["sic"] = f"%{sic_filter}%"
    if name_filter:
        query += " AND c.name ILIKE :name"
        params["name"] = f"%{name_filter}%"
    if events_only:
        query += """ AND EXISTS (SELECT 1 FROM ch_events e
                                 WHERE e.company_number = c.company_number)"""
    query += " ORDER BY s.score DESC, c.date_of_creation DESC LIMIT 500"
    with _engine.connect() as conn:
        return pd.read_sql(text(query), conn, params=params)


def _clear_caches():
    _get_stats.clear()
    _get_companies.clear()


# ==========================================
# CONTACT DETAILS (for a claimed lead)
# ==========================================
def _split_name(full):
    """Best-effort first/last from a Companies House officer name, which is
    usually 'SURNAME, Forename Middlenames'. Rough is fine here."""
    if not full:
        return "", ""
    full = full.strip()
    if "," in full:                                   # "SMITH, John David"
        last, rest = full.split(",", 1)
        first = rest.strip().split()[0] if rest.strip() else ""
        return first.title(), last.strip().title()
    parts = full.split()                              # "John Smith"
    if len(parts) == 1:
        return parts[0].title(), ""
    return parts[0].title(), parts[-1].title()


def _email_from_company(name):
    """A ROUGH guess: info@<company name minus Ltd/Limited, squashed>.co.uk.
    Deliberately not verified — it's a starting point for outreach."""
    if not name:
        return ""
    core = re.sub(r"\b(ltd|limited)\b\.?", "", name, flags=re.I)
    core = re.sub(r"[^a-z0-9]", "", core.lower())
    return f"info@{core}.co.uk" if core else ""


def _contact_details(engine, number, company_name):
    """First/last (from the first director on record) + company + guessed email."""
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT name FROM ch_officers WHERE company_number = :n "
            "AND role = 'director' ORDER BY id LIMIT 1"), {"n": number}).first()
    first, last = _split_name(row[0] if row else "")
    return {"first": first, "last": last,
            "company": company_name or "",
            "email": _email_from_company(company_name)}


def _render_contact_card(details):
    """Contact details, each with a real one-click Copy button. Rendered as a
    small self-contained HTML component (with a clipboard fallback), styled with
    its own light card so it's readable on either Streamlit theme."""
    rows = [("First Name", details["first"]), ("Last Name", details["last"]),
            ("Company Name", details["company"]), ("Email", details["email"])]
    body = ""
    for label, value in rows:
        v = html.escape(value or "", quote=True)
        body += (
            f'<div class="r"><span class="l">{label}:</span>'
            f'<span class="v">{v or "—"}</span>'
            f'<button class="c" data-copy="{v}">Copy</button></div>'
        )
    components.html(f"""
    <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;
                background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;
                padding:14px;color:#111827;">
      <style>
        .r{{display:flex;align-items:center;gap:10px;margin:8px 0;}}
        .l{{font-weight:600;min-width:120px;}}
        .v{{flex:1;background:#f3f4f6;padding:6px 10px;border-radius:6px;
            font-family:monospace;overflow-wrap:anywhere;}}
        .c{{cursor:pointer;border:1px solid #d1d5db;background:#fff;
            border-radius:6px;padding:6px 12px;font-weight:600;}}
        .c:hover{{background:#eef2ff;}}
      </style>
      {body}
      <script>
        document.querySelectorAll('button.c').forEach(function(b){{
          b.addEventListener('click', function(){{
            var t=b.getAttribute('data-copy');
            function done(){{var o=b.textContent;b.textContent='Copied!';
              setTimeout(function(){{b.textContent=o;}},1000);}}
            if(navigator.clipboard&&navigator.clipboard.writeText){{
              navigator.clipboard.writeText(t).then(done).catch(fb);
            }}else{{fb();}}
            function fb(){{var ta=document.createElement('textarea');ta.value=t;
              document.body.appendChild(ta);ta.select();
              try{{document.execCommand('copy');}}catch(e){{}}
              document.body.removeChild(ta);done();}}
          }});
        }});
      </script>
    </div>
    """, height=210)


def _render_my_pipe(engine, username):
    """The current user's claimed leads, each with its copy-ready contact card."""
    with engine.connect() as conn:
        claims = pd.read_sql(text("""
            SELECT c.company_number, c.name, cl.claimed_at
            FROM ch_claims cl
            JOIN ch_companies c ON c.company_number = cl.company_number
            WHERE cl.username = :u
            ORDER BY cl.claimed_at DESC
        """), conn, params={"u": username})

    with st.expander(f"📋 My pipe — claimed leads ({len(claims)})",
                     expanded=not claims.empty):
        if claims.empty:
            st.caption("Claim a lead with **Add to pipe** below and its contact "
                       "details will appear here, ready to copy.")
            return
        for r in claims.itertuples():
            st.markdown(f"**{r.name}**  ·  [{r.company_number}]"
                        f"({CH_LINK}{r.company_number})")
            _render_contact_card(_contact_details(engine, r.company_number, r.name))
            if st.button("↩️ Release back to the board",
                         key=f"release_{r.company_number}"):
                with engine.begin() as conn:
                    conn.execute(text(
                        "DELETE FROM ch_claims WHERE company_number = :n "
                        "AND username = :u"), {"n": r.company_number, "u": username})
                _clear_caches()
                st.rerun()
            st.divider()


# ==========================================
# ADMIN CONTROLS
# ==========================================
def _render_admin_controls(engine):
    from ch_backfill import backfill
    from ch_enrich import drain_queue
    from ch_digest import build_digest
    from ch_enrich import sweep_events

    with st.expander("🛠️ Pipeline controls (admin)"):
        st.caption(
            "Small batches only from here — Streamlit Cloud times out on long "
            "jobs. For volume, run locally: `python ch_backfill.py` then "
            "`python ch_run_local.py`. Real-time new incorporations come from "
            "the `python ch_stream.py companies` listener (an always-on machine, "
            "not Streamlit Cloud); SH01/MR01 trigger events are found over REST."
        )

        col1, col2 = st.columns(2)
        with col1:
            days = st.number_input("Backfill: days of incorporations", 1, 14, 2)
            cap = st.number_input("...capped at companies/day", 50, 5000, 300, step=50)
            if st.button("📡 Ingest new incorporations", use_container_width=True):
                with st.spinner(f"Fetching the last {days} day(s) from Companies House..."):
                    new = backfill(days=int(days), daily_cap=int(cap))
                _clear_caches()
                st.success(f"{new} new companies queued for enrichment.")

        with col2:
            batch = st.number_input("Enrich & score: companies this run", 5, 200, 25, step=5)
            if st.button("🧠 Enrich & score queued companies", use_container_width=True):
                progress = st.progress(0.0, text="Starting...")

                def _on_progress(done, total, number):
                    progress.progress(done / total if total else 1.0,
                                      text=f"{done}/{total} — {number}")

                counts = drain_queue(limit=int(batch), progress_callback=_on_progress)
                progress.empty()
                _clear_caches()
                st.success(f"Scored {counts['scored']}, awaiting PSC recheck "
                           f"{counts['recheck']}, failed {counts['failed']}.")

        ecol1, ecol2 = st.columns([1, 2])
        with ecol1:
            sweep_n = st.number_input("Event check: companies", 10, 500, 100, step=10)
        with ecol2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("⚡ Check for SH01/MR01 trigger events (REST)",
                         use_container_width=True,
                         help="Re-checks the youngest tracked companies for fresh "
                              "share allotments / new charges and promotes them to "
                              "Tier 1. This is the REST alternative to the /filings "
                              "stream."):
                progress = st.progress(0.0, text="Starting...")

                def _on_ev(done, total, number):
                    progress.progress(done / total if total else 1.0,
                                      text=f"{done}/{total} — {number}")

                applied = sweep_events(limit=int(sweep_n), progress_callback=_on_ev)
                progress.empty()
                _clear_caches()
                st.success(f"{applied} new trigger event(s) applied "
                           "(promoted to Tier 1).")

        st.divider()
        st.markdown("**Daily digest** — Tier 1/2 companies newly scored or "
                    "promoted in the window:")
        dcol1, dcol2, dcol3 = st.columns([1, 1, 1])
        with dcol1:
            hours = st.selectbox("Window", [24, 48, 72, None],
                                 format_func=lambda h: "all current" if h is None else f"last {h}h")
        markdown, csv_text, count = build_digest(hours)
        stamp = pd.Timestamp.utcnow().strftime("%Y-%m-%d")
        with dcol2:
            st.download_button(f"⬇️ Markdown ({count})", markdown,
                               file_name=f"digest-{stamp}.md",
                               use_container_width=True)
        with dcol3:
            st.download_button(f"⬇️ CSV ({count})", csv_text,
                               file_name=f"digest-{stamp}.csv",
                               use_container_width=True)


# ==========================================
# DRILLDOWN
# ==========================================
def _render_drilldown(engine, df, username):
    st.markdown("### 🔍 Company drilldown")
    options = {
        f"{row.name} ({row.company_number}) — {row.score}": row.company_number
        for row in df.itertuples()
    }
    choice = st.selectbox("Pick a company", list(options.keys()))
    if not choice:
        return
    number = options[choice]
    row = df[df.company_number == number].iloc[0]

    left, right = st.columns([1, 1])
    with left:
        st.markdown(f"**[Open on Companies House]({CH_LINK}{number})**")
        st.write(f"Incorporated: {row.date_of_creation}")
        st.write(f"SIC codes: {row.sic_codes or '—'}")
        st.metric("Score", f"{row.score} (Tier {row.tier})")

    with right:
        st.markdown("**Why this score:**")
        breakdown = row.breakdown or {}
        lines = [
            {"Signal": SIGNAL_LABELS.get(k, k), "Points": v}
            for k, v in breakdown.items() if isinstance(v, int)
        ]
        if lines:
            st.dataframe(pd.DataFrame(lines), hide_index=True,
                         use_container_width=True)
        if breakdown.get("disqualified"):
            st.warning(f"Disqualified — {breakdown['disqualified']}")
        if breakdown.get("events"):
            st.info("Trigger events: " + ", ".join(breakdown["events"]))

    with st.expander("People, capital & events"):
        with engine.connect() as conn:
            psc = pd.read_sql(text(
                "SELECT kind, name, country FROM ch_psc "
                "WHERE company_number = :n"), conn, params={"n": number})
            officers = pd.read_sql(text(
                "SELECT name, role, correspondence_country, prior_appointments, "
                "quality_flag FROM ch_officers WHERE company_number = :n"),
                conn, params={"n": number})
            capital = pd.read_sql(text(
                "SELECT filing_type, currency, figure, filing_date "
                "FROM ch_capital_statements WHERE company_number = :n"),
                conn, params={"n": number})
            # detail::text — a raw JSONB dict column upsets the dataframe
            # serializer, and the JSON string reads fine anyway.
            events = pd.read_sql(text(
                "SELECT event_type, detail::text AS detail, occurred_at "
                "FROM ch_events WHERE company_number = :n "
                "ORDER BY occurred_at DESC"),
                conn, params={"n": number})
        for title, table in (("PSCs", psc), ("Officers", officers),
                             ("Capital statements", capital), ("Events", events)):
            st.markdown(f"**{title}**")
            if table.empty:
                st.caption("None recorded.")
            else:
                st.dataframe(table, hide_index=True, use_container_width=True)

    st.markdown("#### Actions")
    bcol1, bcol2 = st.columns(2)
    with bcol1:
        if st.button("👎 Pass", use_container_width=True,
                     help="Hide this company from YOUR board. Teammates still see it."):
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO ch_passes (company_number, username)
                    VALUES (:n, :u) ON CONFLICT DO NOTHING
                """), {"n": number, "u": username})
            _clear_caches()
            st.rerun()
    with bcol2:
        if st.button("✅ Add to pipe", type="primary", use_container_width=True,
                     help="Claim this lead — it's removed from everyone else's board "
                          "and its contact details appear in 'My pipe' at the top."):
            with engine.begin() as conn:
                res = conn.execute(text("""
                    INSERT INTO ch_claims (company_number, username)
                    VALUES (:n, :u) ON CONFLICT (company_number) DO NOTHING
                """), {"n": number, "u": username})
            _clear_caches()
            if res.rowcount:
                st.toast(f"Added {row['name']} to your pipe ✅")
            else:
                st.toast("Someone else just claimed this one.", icon="⚠️")
            st.rerun()


# ==========================================
# THE PAGE
# ==========================================
def render(engine, role):
    username = st.session_state.get("username", "")

    head_l, head_r = st.columns([4, 1])
    with head_l:
        st.title("💎 High Quality New Incorps")
    with head_r:
        st.write("")  # nudge the button down to line up with the title
        if st.button("🔄 Refresh", use_container_width=True,
                     help="Reload the latest scored companies. Does NOT reload the "
                          "browser, so it won't log you out."):
            _clear_caches()
            st.rerun()

    st.write(
        "Newly incorporated UK companies with high expected banking usage — "
        "foreign parents, real paid-up capital, FX-heavy sectors — scored and "
        "tiered. Fed by Companies House, refreshed by the backfill/stream "
        "listeners."
    )

    _render_my_pipe(engine, username)

    stats = _get_stats(engine)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tier 1 (high-touch)", stats.get("tier1", 0))
    c2.metric("Tier 2 (sequence)", stats.get("tier2", 0))
    c3.metric("Awaiting enrichment", stats.get("queued", 0),
              help="Ingested but not yet enriched/scored. Run "
                   "'python ch_run_local.py' or the admin button below.")
    c4.metric("Trigger events (7d)", stats.get("events_7d", 0),
              help="SH01 share allotments / MR01 charges on tracked young "
                   "companies — these promote straight to Tier 1.")

    if role == "admin":
        _render_admin_controls(engine)

    st.divider()

    # --- FILTERS ---
    fcol1, fcol2, fcol3, fcol4 = st.columns([1.2, 1, 1, 1])
    with fcol1:
        tiers = st.multiselect("Tier", [1, 2, 3], default=[1, 2],
                               help="Tier 3 (< 30) is stored but hidden by default.")
    with fcol2:
        sic_filter = st.text_input("SIC contains", placeholder="e.g. 46")
    with fcol3:
        name_filter = st.text_input("Name contains", placeholder="e.g. trading")
    with fcol4:
        events_only = st.checkbox("Trigger events only",
                                  help="Only companies with an SH01/MR01 event.")

    if not tiers:
        st.info("Pick at least one tier to see companies.")
        return

    df = _get_companies(engine, tiers, sic_filter.strip(), name_filter.strip(),
                        events_only, username)
    if df.empty:
        st.info(
            "No scored companies match. If the engine is brand new: an admin "
            "should run the backfill and enrichment (controls above, or "
            "`python ch_backfill.py` + `python ch_run_local.py` locally)."
        )
        return

    display = df.assign(
        ch_link=CH_LINK + df["company_number"],
        event=df["has_event"].map(lambda x: "⚡" if x else ""),
    )[["name", "company_number", "score", "tier", "event",
       "date_of_creation", "sic_codes", "ch_link"]]
    st.dataframe(
        display,
        hide_index=True, use_container_width=True,
        column_config={
            "name": "Company",
            "company_number": "Number",
            "score": st.column_config.NumberColumn("Score"),
            "tier": "Tier",
            "event": st.column_config.TextColumn(
                "⚡", help="Has a trigger event (fresh raise / new charge)"),
            "date_of_creation": "Incorporated",
            "sic_codes": "SIC",
            "ch_link": st.column_config.LinkColumn(
                "Companies House", display_text="open"),
        },
    )
    st.caption(f"{len(df)} companies shown (capped at 500).")

    st.divider()
    _render_drilldown(engine, df, username)
