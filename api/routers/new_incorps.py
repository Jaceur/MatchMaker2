"""Live new-incorporations stream — real-time, two channels, with claims.

CHStream POSTs each new company to `/new-incorps/ingest`; browsers hold an SSE
connection to `/new-incorps/stream?channel=all|high_value` and show the newest
25, oldest falling off. The **High-Value** channel only carries leads meeting one
of: starting capital > £50k, corporate ownership, or a London Zone-1 postcode.

The stream is ephemeral (in-memory ring buffer per channel). The exception is a
CLAIM: when an AE copies a company, the browser calls `/claim` — it's stored in
`new_incorp_claims` (durable, "for later"), and a `claim` event is broadcast so
every open page greys it out. Claimed status is DB-backed, so a lead stays greyed
across reloads and for AEs who connect later. First claim wins.

Architecture note: the broker is IN-PROCESS — ingest, claims and every SSE client
must share one API instance (true today: one Railway API service). Scaling the
API to >1 instance would need a shared bus (Redis) for the fan-out.
"""
import asyncio
import json
import re
from collections import deque
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import engine
from models import new_incorp_claims
from ..config import settings
from ..security import get_current_user, CurrentUser

router = APIRouter(prefix="/new-incorps", tags=["new-incorps"])

MAX_ON_SCREEN = 25
_CLIENT_QUEUE_MAX = 100
_HEARTBEAT_SECONDS = 15
CHANNELS = ("all", "high_value")

# ---- High-Value criteria (any one qualifies) ----
CAPITAL_THRESHOLD = 50_000       # starting capital strictly ABOVE this
# London Zone-1 postcode OUTWARD codes (broad-central set, confirmed with the
# user): EC*, WC*, W1, SW1, SE1, NW1, N1, E1. The patterns exclude neighbours by
# district number — W1 but not W10, N1 but not N10, E1 but not E14, etc.
_ZONE1_PATTERNS = [re.compile(p) for p in (
    r"^EC[1-4][A-Z]?$", r"^WC[12][A-Z]?$", r"^W1[A-Z]?$", r"^SW1[A-Z]?$",
    r"^SE1[A-Z]?$", r"^NW1[A-Z]?$", r"^N1[A-Z]?$", r"^E1[A-Z]?$",
)]


def _outward_code(postcode: str) -> str:
    """The outward part of a UK postcode ('SW1A 1AA' -> 'SW1A'). The inward code
    is always the last 3 chars, so strip those after removing spaces."""
    pc = (postcode or "").upper().replace(" ", "")
    return pc[:-3] if len(pc) > 3 else pc


def is_zone1(postcode) -> bool:
    ow = _outward_code(str(postcode or ""))
    return any(p.match(ow) for p in _ZONE1_PATTERNS)


def is_high_value(event: dict) -> bool:
    """Any one of: capital > £50k, corporate owner, or a Zone-1 postcode."""
    cap = event.get("starting_capital")
    try:
        cap_ok = cap is not None and cap != "" and float(cap) > CAPITAL_THRESHOLD
    except (TypeError, ValueError):
        cap_ok = False
    return bool(cap_ok or event.get("corporate_owner") or is_zone1(event.get("postcode")))


class _Broker:
    """In-process fan-out with two channels + a DB-backed claim set. One event
    loop (all endpoints async), so no locking needed."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = {c: set() for c in CHANNELS}
        self._buffers: dict[str, deque] = {c: deque(maxlen=MAX_ON_SCREEN) for c in CHANNELS}
        self._claimed: dict[str, str] = {}   # company_number -> claimed_by

    def load_claims(self, claims: dict[str, str]) -> None:
        self._claimed.update(claims)

    def _annotate(self, event: dict) -> dict:
        by = self._claimed.get(event.get("company_number"))
        return {**event, "claimed": True, "claimed_by": by} if by else event

    def publish_incorp(self, event: dict) -> int:
        event = self._annotate(event)
        targets = ["all"] + (["high_value"] if is_high_value(event) else [])
        msg = ("incorp", event)
        for ch in targets:
            self._buffers[ch].append(event)
            for q in list(self._subs[ch]):
                _offer(q, msg)
        return sum(len(self._subs[ch]) for ch in targets)

    def register_claim(self, company_number: str, claimed_by: str) -> None:
        self._claimed[company_number] = claimed_by
        msg = ("claim", {"company_number": company_number, "claimed_by": claimed_by})
        for ch in CHANNELS:
            for q in list(self._subs[ch]):
                _offer(q, msg)

    def is_claimed(self, company_number: str) -> str | None:
        return self._claimed.get(company_number)

    def subscribe(self, channel: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=_CLIENT_QUEUE_MAX)
        self._subs[channel].add(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        self._subs[channel].discard(q)

    def snapshot(self, channel: str) -> list[dict]:
        """Current window, oldest-first, re-annotated with live claim status (a
        lead claimed after it was buffered still shows greyed to a new joiner)."""
        return [self._annotate(ev) for ev in self._buffers[channel]]


def _offer(q: asyncio.Queue, msg) -> None:
    try:
        q.put_nowait(msg)
    except asyncio.QueueFull:
        pass  # a stalled tab — skip it rather than block everyone


broker = _Broker()


def load_claims_into_broker() -> None:
    """Seed the in-memory claim set from the DB (called at API startup, so claims
    survive a restart / redeploy)."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                select(new_incorp_claims.c.company_number, new_incorp_claims.c.claimed_by)
            ).fetchall()
        broker.load_claims({cn: by for cn, by in rows})
        print(f"new-incorps: loaded {len(rows)} existing claims.")
    except Exception as e:
        print(f"new-incorps: could not load claims ({e}) — starting empty.")


class IncorpIn(BaseModel):
    model_config = {"extra": "allow"}
    company_number: str
    company_name: str | None = None
    date_of_creation: str | None = None
    sic_codes: list[str] | str | None = None


class ClaimIn(BaseModel):
    """The lead being claimed. company_number is the key; the rest is stored
    whole ('for later reasons')."""
    model_config = {"extra": "allow"}
    company_number: str
    company_name: str | None = None


def _sse(kind: str, data: dict) -> str:
    if kind == "claim":
        return f"event: claim\ndata: {json.dumps(data, default=str)}\n\n"
    return f"data: {json.dumps(data, default=str)}\n\n"


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest(body: IncorpIn, x_ingest_key: str = Header(default="")):
    """CHStream calls this per new incorporation (X-Ingest-Key auth)."""
    if not settings.new_incorp_ingest_key:
        raise HTTPException(status_code=503, detail="Ingest not configured (set NEW_INCORP_INGEST_KEY).")
    if x_ingest_key != settings.new_incorp_ingest_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad ingest key.")
    event = body.model_dump()
    event["received_at"] = datetime.now(timezone.utc).isoformat()
    event["high_value"] = is_high_value(event)
    listeners = broker.publish_incorp(event)
    return {"ok": True, "listeners": listeners, "high_value": event["high_value"]}


@router.post("/claim", status_code=status.HTTP_200_OK)
async def claim(body: ClaimIn, user: CurrentUser = Depends(get_current_user)):
    """An AE claimed a lead (copied it). Persist it (first claim wins) and
    broadcast so every open page greys it out. Returns who owns it."""
    lead = body.model_dump()
    existing = broker.is_claimed(body.company_number)
    if existing:
        return {"ok": True, "claimed_by": existing, "already": True}

    with engine.begin() as conn:
        conn.execute(
            pg_insert(new_incorp_claims)
            .values(
                company_number=body.company_number,
                company_name=body.company_name,
                claimed_by=user.username,
                claimed_at=datetime.utcnow(),
                lead=lead,
            )
            .on_conflict_do_nothing(index_elements=["company_number"])
        )
        # Read back the actual owner (handles a race where another AE won).
        owner = conn.execute(
            select(new_incorp_claims.c.claimed_by)
            .where(new_incorp_claims.c.company_number == body.company_number)
        ).scalar()

    owner = owner or user.username
    broker.register_claim(body.company_number, owner)
    return {"ok": True, "claimed_by": owner, "already": owner != user.username}


# ---------------------------------------------------------------------------
# The claimer's outreach pipeline (small — there will be a LOT of these)
# ---------------------------------------------------------------------------
class StepUpdate(BaseModel):
    step: str            # e.g. "connection_request" | "inmail" | "follow_up"
    value: bool


class ArchiveIn(BaseModel):
    outcome: str         # "success" | "removed"


@router.get("/pipeline")
def pipeline(user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    """This AE's ACTIVE claimed incorps (not yet archived), newest first."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(new_incorp_claims)
            .where(new_incorp_claims.c.claimed_by == user.username)
            .where(new_incorp_claims.c.outcome.is_(None))
            .order_by(new_incorp_claims.c.claimed_at.desc())
        ).mappings().fetchall()
    return [dict(r) for r in rows]


def _owned_claim(conn, company_number: str, username: str):
    """Fetch a still-active claim that belongs to this AE, or raise 404/403."""
    row = conn.execute(
        select(new_incorp_claims).where(new_incorp_claims.c.company_number == company_number)
    ).mappings().fetchone()
    if not row or row["outcome"] is not None:
        raise HTTPException(status_code=404, detail="Not in your pipeline.")
    if row["claimed_by"] != username:
        raise HTTPException(status_code=403, detail="Not your lead.")
    return row


@router.post("/pipeline/{company_number}/step")
def set_step(company_number: str, body: StepUpdate, user: CurrentUser = Depends(get_current_user)) -> dict:
    """Tick/untick one outreach step. Steps are a free-form bool map so the set
    can grow later without a schema change."""
    with engine.begin() as conn:
        row = _owned_claim(conn, company_number, user.username)
        steps = dict(row["steps"] or {})
        steps[body.step] = body.value
        conn.execute(
            update(new_incorp_claims)
            .where(new_incorp_claims.c.company_number == company_number)
            .values(steps=steps)
        )
    return {"steps": steps}


@router.post("/pipeline/{company_number}/archive")
def archive(company_number: str, body: ArchiveIn, user: CurrentUser = Depends(get_current_user)) -> dict:
    """Success or Remove: archive the lead (it leaves the pipeline; the row stays
    in the DB tagged with the outcome). The claim itself persists, so the company
    stays greyed on the stream — it won't be re-picked."""
    if body.outcome not in ("success", "removed"):
        raise HTTPException(status_code=400, detail="outcome must be 'success' or 'removed'.")
    with engine.begin() as conn:
        _owned_claim(conn, company_number, user.username)
        conn.execute(
            update(new_incorp_claims)
            .where(new_incorp_claims.c.company_number == company_number)
            .values(outcome=body.outcome, archived_at=datetime.utcnow())
        )
    return {"ok": True, "outcome": body.outcome}


def _valid_token(token: str) -> bool:
    if not token:
        return False
    try:
        jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return True
    except jwt.PyJWTError:
        return False


@router.get("/stream")
async def stream(request: Request, token: str = "", channel: str = "all"):
    """SSE feed for a channel ('all' or 'high_value'). Replays the current window
    (with live claim status), then streams incorp + claim events."""
    if channel not in CHANNELS:
        raise HTTPException(status_code=400, detail="Unknown channel.")
    if not _valid_token(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing token.")

    async def gen():
        q = broker.subscribe(channel)
        try:
            for event in broker.snapshot(channel):
                yield _sse("incorp", event)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    kind, data = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_SECONDS)
                    yield _sse(kind, data)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            broker.unsubscribe(channel, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
