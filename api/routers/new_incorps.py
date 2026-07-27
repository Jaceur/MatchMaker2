"""Live new-incorporations stream — a real-time, ephemeral feed.

CHStream (the separate Railway service) POSTs each newly-incorporated company to
`/new-incorps/ingest`; the browser holds an SSE connection to `/new-incorps/stream`
and shows the newest 25, oldest falling off the bottom. NOTHING is persisted —
the only state is an in-memory ring buffer of the last 25, so a page opened mid-
stream immediately sees the current window instead of a blank screen. It's lost
on restart, by design.

Architecture note: the broker is IN-PROCESS, so ingest and every SSE client must
live in the SAME API instance. That's true today (one Railway API service). If
the API is ever scaled to >1 instance, this needs a shared bus (Redis pub/sub)
or the fan-out breaks — an ingest on instance A won't reach a client on B.
"""
import asyncio
import json
from collections import deque
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..config import settings

router = APIRouter(prefix="/new-incorps", tags=["new-incorps"])

MAX_ON_SCREEN = 25          # the rolling window the page shows
_CLIENT_QUEUE_MAX = 100     # per-client backlog before we drop (slow tab safety)
_HEARTBEAT_SECONDS = 15     # keep the SSE connection alive through proxies


class _Broker:
    """In-process fan-out: one asyncio.Queue per connected browser, plus a
    deque of the last MAX_ON_SCREEN events to seed newly-connected clients.
    Single event loop (all endpoints are async), so no locking needed."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._buffer: deque = deque(maxlen=MAX_ON_SCREEN)

    def publish(self, event: dict) -> int:
        self._buffer.append(event)
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # a stalled tab — skip it rather than block everyone
        return len(self._subscribers)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=_CLIENT_QUEUE_MAX)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def snapshot(self) -> list[dict]:
        """Current window, OLDEST-first (the client prepends, so newest ends up
        on top and the order matches a live fill)."""
        return list(self._buffer)


_broker = _Broker()


class IncorpIn(BaseModel):
    """One newly-incorporated company from CHStream. Only company_number is
    required; everything else is best-effort display data. Extra keys are kept."""
    model_config = {"extra": "allow"}
    company_number: str
    company_name: str | None = None
    date_of_creation: str | None = None
    sic_codes: list[str] | str | None = None


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest(body: IncorpIn, x_ingest_key: str = Header(default="")):
    """CHStream calls this per new incorporation. Protected by a shared secret
    (X-Ingest-Key) so nothing but CHStream can inject into the feed."""
    if not settings.new_incorp_ingest_key:
        raise HTTPException(status_code=503, detail="Ingest not configured (set NEW_INCORP_INGEST_KEY).")
    if x_ingest_key != settings.new_incorp_ingest_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad ingest key.")

    event = body.model_dump()
    # Server-stamped arrival time — the page orders by this, and it's the honest
    # "when we saw it" even if date_of_creation lags.
    event["received_at"] = datetime.now(timezone.utc).isoformat()
    listeners = _broker.publish(event)
    return {"ok": True, "listeners": listeners}


def _valid_token(token: str) -> bool:
    if not token:
        return False
    try:
        jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return True
    except jwt.PyJWTError:
        return False


@router.get("/stream")
async def stream(request: Request, token: str = ""):
    """SSE feed. The page keeps this open and appends events as they arrive.

    EventSource can't send an Authorization header, so the JWT comes as a query
    param (the data is public CH records, but the PAGE is behind login, so we
    still check it). On connect we replay the current window, then stream live."""
    if not _valid_token(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing token.")

    async def gen():
        q = _broker.subscribe()
        try:
            # Seed the newcomer with the window so it isn't blank.
            for event in _broker.snapshot():
                yield _sse(event)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_SECONDS)
                    yield _sse(event)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"   # comment line — keeps proxies open
        finally:
            _broker.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
