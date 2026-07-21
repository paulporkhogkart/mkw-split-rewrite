from __future__ import annotations

import asyncio

import pytest

from hotline.config import Config
from hotline.controller import Controller, PhoneUnplugged
from hotline.db import Db
from hotline.events import EventBus
from hotline.lease import LineBusy


async def make_ctl(tmp_path, unused_tcp_port, **env):
    e = {"HOTLINE_ENV": "dev", "HOTLINE_DATA_DIR": str(tmp_path),
         "HOTLINE_AUDIOSOCKET_PORT": str(unused_tcp_port), "HOTLINE_ECHO": "1"}
    e.update(env)
    cfg = Config.from_env(e)
    bus = EventBus(delay_n=0.05)
    await bus.start()
    db = Db(tmp_path / "h.db"); db.init()
    ctl = Controller(cfg, bus, db, phone_leg_factory=None)
    await ctl.start()
    return cfg, bus, db, ctl


async def teardown(bus, db, ctl):
    await ctl.stop(); await bus.stop(); db.close()


async def attach(ctl, lease_id):
    sent = []
    async def send(frame): sent.append(frame)
    await ctl.attach_caller_ws(send, lease_id=lease_id)
    return sent


async def test_claim_ring_answer_hangup_flow(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_ctl(tmp_path, unused_tcp_port)
    q = bus.subscribe("rt")
    lid = ctl.claim_line()
    with pytest.raises(LineBusy):
        ctl.claim_line()
    await attach(ctl, lid)
    call_id = await ctl.ring_with_lease(lid)
    assert call_id
    # echo leg answers instantly -> lease should be oncall
    assert ctl.lease.state == "oncall"
    assert await ctl.hangup_with_lease(lid) is True
    await asyncio.sleep(0.1)  # reap
    assert ctl.lease.state == "idle"
    states = [e["state"] for e in _drain(q) if e.get("type") == "line_state"]
    assert states[-1] == "idle"
    assert "oncall" in states and "held" in states
    await teardown(bus, db, ctl)


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


async def test_ring_requires_matching_lease(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_ctl(tmp_path, unused_tcp_port)
    with pytest.raises(KeyError):
        await ctl.ring_with_lease("not-a-lease")
    await teardown(bus, db, ctl)


async def test_claim_window_frees_the_line(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_ctl(tmp_path, unused_tcp_port,
                                     HOTLINE_CLAIM_WINDOW_S="0.05")
    ctl.claim_line()
    await asyncio.sleep(0.2)
    assert ctl.lease.state == "idle"     # expired and auto-released
    ctl.claim_line()                     # line reusable
    await teardown(bus, db, ctl)


async def test_backstop_ends_live_call(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_ctl(tmp_path, unused_tcp_port,
                                     HOTLINE_CALL_BACKSTOP_S="1")
    lid = ctl.claim_line()
    await attach(ctl, lid)
    call_id = await ctl.ring_with_lease(lid)
    await asyncio.sleep(1.5)
    assert ctl.lease.state == "idle"
    row = db._conn.execute("SELECT outcome FROM calls WHERE call_id=?",
                           (call_id,)).fetchone()
    assert row and row[0] in ("dropped", "completed")
    await teardown(bus, db, ctl)


async def test_unplugged_refuses_claims_and_snapshots(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_ctl(tmp_path, unused_tcp_port)
    ctl.set_phone_reachable(False)
    assert ctl.line_snapshot()["state"] == "unplugged"
    with pytest.raises(PhoneUnplugged):
        ctl.claim_line()
    ctl.set_phone_reachable(True)
    assert ctl.line_snapshot()["state"] == "idle"
    ctl.claim_line()
    await teardown(bus, db, ctl)


async def test_ws_drop_grace_releases_lease(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_ctl(tmp_path, unused_tcp_port,
                                     HOTLINE_WS_GRACE_S="0.05")
    lid = ctl.claim_line()
    await attach(ctl, lid)
    await ctl.ring_with_lease(lid)
    ctl.detach_caller_ws()               # tab died
    await asyncio.sleep(0.5)             # grace 0.05 + teardown
    assert ctl.lease.state == "idle"
    await teardown(bus, db, ctl)


async def test_echo_ring_delay_rings_before_answering(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_ctl(tmp_path, unused_tcp_port,
                                     HOTLINE_ECHO_RING_S="0.3")
    lid = ctl.claim_line()
    await attach(ctl, lid)
    await ctl.ring_with_lease(lid)
    assert ctl.lease.state == "ringing"     # not answered yet
    await asyncio.sleep(0.6)
    assert ctl.lease.state == "oncall"      # echo answered after the delay
    await ctl.hangup_with_lease(lid)
    await teardown(bus, db, ctl)
