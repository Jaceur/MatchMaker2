"""The in-memory broker behind the live new-incorps stream.

Pure asyncio (driven via asyncio.run so no pytest-asyncio dependency), no HTTP,
no DB. Covers the fan-out, the 25-item ring buffer, and unsubscribe cleanup —
the logic the real-time feed rests on.
"""
import asyncio
import os

os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("SUPABASE_HOST", "localhost")
os.environ.setdefault("SUPABASE_USER", "test")

from api.routers.new_incorps import MAX_ON_SCREEN, _Broker, _sse  # noqa: E402


def test_publish_fans_out_to_every_subscriber():
    async def scenario():
        b = _Broker()
        q1, q2 = b.subscribe(), b.subscribe()
        n = b.publish({"company_number": "12345678"})
        assert n == 2
        assert (await q1.get())["company_number"] == "12345678"
        assert (await q2.get())["company_number"] == "12345678"
    asyncio.run(scenario())


def test_buffer_keeps_only_the_last_25_oldest_first():
    async def scenario():
        b = _Broker()
        for i in range(40):
            b.publish({"company_number": str(i)})
        snap = b.snapshot()
        assert len(snap) == MAX_ON_SCREEN
        # oldest-first, and it's the last 25 (15..39)
        assert snap[0]["company_number"] == "15"
        assert snap[-1]["company_number"] == "39"
    asyncio.run(scenario())


def test_a_new_subscriber_only_gets_events_after_it_joins():
    """The buffer seeds the initial window (via snapshot); the queue only
    carries events published after subscribing — no double-delivery."""
    async def scenario():
        b = _Broker()
        b.publish({"company_number": "before"})
        q = b.subscribe()
        b.publish({"company_number": "after"})
        assert q.qsize() == 1
        assert (await q.get())["company_number"] == "after"
    asyncio.run(scenario())


def test_unsubscribe_stops_delivery():
    async def scenario():
        b = _Broker()
        q = b.subscribe()
        b.unsubscribe(q)
        assert b.publish({"company_number": "x"}) == 0
        assert q.qsize() == 0
    asyncio.run(scenario())


def test_a_full_slow_client_never_blocks_the_others():
    """A stalled tab whose queue fills must not stop a healthy tab getting events."""
    async def scenario():
        b = _Broker()
        slow, fast = b.subscribe(), b.subscribe()
        # jam the slow client to its cap without draining it
        while not slow.full():
            slow.put_nowait({"company_number": "filler"})
        b.publish({"company_number": "live"})   # must not raise despite slow being full
        # the healthy client still received the live event
        assert (await fast.get())["company_number"] == "live"
    asyncio.run(scenario())


def test_sse_framing():
    line = _sse({"company_number": "1", "company_name": "ACME LTD"})
    assert line.startswith("data: ")
    assert line.endswith("\n\n")
    assert '"company_number": "1"' in line
