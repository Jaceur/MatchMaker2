"""The auto-source scheduler's decision logic (lead_worker._auto_source_decision).

Pure — no DB — so every branch of "should the worker enqueue a scheduled
source+enrich job right now?" is nailed down here.
"""
import os

os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("SUPABASE_HOST", "localhost")
os.environ.setdefault("SUPABASE_USER", "test")

from lead_worker import _auto_source_decision  # noqa: E402


def decide(**over):
    base = dict(enabled=True, job_in_flight=False, elapsed_hours=5.0,
                interval_hours=4, awaiting=200, cap=1000)
    base.update(over)
    return _auto_source_decision(**base)[0]


def test_sources_when_due_and_buffer_low():
    assert decide() is True


def test_disabled_never_sources():
    assert decide(enabled=False) is False


def test_skips_when_a_job_is_already_in_flight():
    """Don't stack jobs — the worker is busy, or a manual job is queued."""
    assert decide(job_in_flight=True) is False


def test_waits_until_the_interval_has_passed():
    assert decide(elapsed_hours=3.9, interval_hours=4) is False
    assert decide(elapsed_hours=4.0, interval_hours=4) is True


def test_never_sourced_yet_is_due():
    """elapsed_hours=None (no last_auto_source_at) → treat as due."""
    assert decide(elapsed_hours=None) is True


def test_pauses_at_or_above_the_cap():
    assert decide(awaiting=1000, cap=1000) is False
    assert decide(awaiting=1500, cap=1000) is False


def test_resumes_just_below_the_cap():
    assert decide(awaiting=999, cap=1000) is True


def test_the_cap_gate_is_independent_of_the_time_gate():
    """Over cap but interval elapsed → still hold off (the buffer rules)."""
    assert decide(elapsed_hours=99, awaiting=1200, cap=1000) is False
    """Under cap but interval NOT elapsed → still wait (the clock rules)."""
    assert decide(elapsed_hours=1, awaiting=10, cap=1000) is False
