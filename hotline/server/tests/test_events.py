from __future__ import annotations

import asyncio
import time

from hotline.events import EventBus


async def test_rt_is_immediate_delayed_waits():
    bus = EventBus(delay_n=0.15)
    await bus.start()
    rt = bus.subscribe("rt")
    delayed = bus.subscribe("delayed")
    t0 = time.monotonic()
    bus.publish({"type": "call_ringing", "call_id": "c1", "caller": "x"})

    ev_rt = await asyncio.wait_for(rt.get(), 0.1)
    assert ev_rt["type"] == "call_ringing" and (time.monotonic() - t0) < 0.1

    ev_d = await asyncio.wait_for(delayed.get(), 1.0)
    assert ev_d["type"] == "call_ringing"
    assert (time.monotonic() - t0) >= 0.14  # held for ~delay_n
    await bus.stop()


async def test_unsubscribe_stops_delivery():
    bus = EventBus(delay_n=0.01)
    await bus.start()
    q = bus.subscribe("rt")
    bus.unsubscribe("rt", q)
    bus.publish({"type": "lines_state", "open": True})
    await asyncio.sleep(0.05)
    assert q.empty()
    await bus.stop()
