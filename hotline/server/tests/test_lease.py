from __future__ import annotations

import asyncio

import pytest

from hotline import lease as lease_mod
from hotline.lease import LineBusy, LineLease


def make(events, **kw):
    kw.setdefault("claim_window_s", 0.15)
    kw.setdefault("backstop_s", 10.0)
    return LineLease(events.append, **kw)


async def test_claim_transitions_and_publishes():
    events: list[dict] = []
    ll = make(events)
    assert ll.state == lease_mod.IDLE
    lid = ll.claim()
    assert ll.state == lease_mod.HELD and ll.valid(lid)
    with pytest.raises(LineBusy):
        ll.claim()
    ll.mark_ringing(lid)
    ll.mark_oncall(lid)
    ll.release(lid)
    assert ll.state == lease_mod.IDLE and not ll.valid(lid)
    assert [e["state"] for e in events] == ["held", "ringing", "oncall", "idle"]
    assert all(e["type"] == "line_state" and "since" in e for e in events)
    assert all("lease" not in e for e in events)  # credential never broadcast


async def test_stale_id_rejected():
    ll = make([])
    lid = ll.claim()
    ll.release(lid)
    with pytest.raises(KeyError):
        ll.mark_ringing(lid)
    ll.release(lid)  # releasing a stale id is a no-op, not an error


async def test_claim_window_expires_held_lease():
    expired: list[str] = []
    ll = make([], claim_window_s=0.05)
    ll.on_expired(expired.append)
    lid = ll.claim()
    await asyncio.sleep(0.15)
    assert expired == [lid]        # callback fired
    # callback owner is responsible for release; simulate it:
    ll.release(lid)
    assert ll.state == lease_mod.IDLE


async def test_ringing_cancels_claim_window():
    expired: list[str] = []
    ll = make([], claim_window_s=0.05)
    ll.on_expired(expired.append)
    lid = ll.claim()
    ll.mark_ringing(lid)
    await asyncio.sleep(0.15)
    assert expired == []           # window no longer applies
    ll.release(lid)


async def test_backstop_fires_even_oncall():
    expired: list[str] = []
    ll = make([], claim_window_s=5.0, backstop_s=0.1)
    ll.on_expired(expired.append)
    lid = ll.claim()
    ll.mark_ringing(lid)
    ll.mark_oncall(lid)
    await asyncio.sleep(0.2)
    assert expired == [lid]


async def test_release_cancels_timers():
    expired: list[str] = []
    ll = make([], claim_window_s=0.05, backstop_s=0.05)
    ll.on_expired(expired.append)
    lid = ll.claim()
    ll.release(lid)
    await asyncio.sleep(0.15)
    assert expired == []
