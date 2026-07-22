from __future__ import annotations

import asyncio

from hotline.config import Config
from hotline.controller import Controller, PhoneOffhook, PhoneUnplugged
from hotline.db import Db
from hotline.events import EventBus


async def make_stack(tmp_path, unused_tcp_port, **env):
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


async def close_stack(ctl, bus, db):
    await ctl.stop(); await bus.stop(); db.close()


async def test_offhook_composes_into_snapshot(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_stack(tmp_path, unused_tcp_port)
    assert ctl.line_snapshot()["state"] == "idle"
    ctl.set_phone_offhook(True)
    assert ctl.line_snapshot()["state"] == "offhook"
    ctl.set_phone_offhook(False)
    assert ctl.line_snapshot()["state"] == "idle"
    await close_stack(ctl, bus, db)


async def test_unplugged_wins_over_offhook(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_stack(tmp_path, unused_tcp_port)
    ctl.set_phone_offhook(True)
    ctl.set_phone_reachable(False)
    assert ctl.line_snapshot()["state"] == "unplugged"
    ctl.set_phone_reachable(True)
    assert ctl.line_snapshot()["state"] == "offhook"
    await close_stack(ctl, bus, db)


async def test_offhook_change_publishes_line_state(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_stack(tmp_path, unused_tcp_port)
    q = bus.subscribe("rt")
    ctl.set_phone_offhook(True)
    ctl.set_phone_offhook(True)          # no-op: must not publish twice
    ev = await asyncio.wait_for(q.get(), 2)
    assert ev["type"] == "line_state" and ev["state"] == "offhook"
    await asyncio.sleep(0.05)            # give a wrongly-published dup time to land
    assert q.empty()
    bus.unsubscribe("rt", q)
    await close_stack(ctl, bus, db)


async def test_claim_refused_while_offhook(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_stack(tmp_path, unused_tcp_port)
    ctl.set_phone_offhook(True)
    try:
        ctl.claim_line()
        raise AssertionError("claim should have raised")
    except PhoneOffhook:
        pass
    ctl.set_phone_offhook(False)
    assert ctl.claim_line()              # line claimable again
    await close_stack(ctl, bus, db)


async def test_unplugged_raised_before_offhook(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_stack(tmp_path, unused_tcp_port)
    ctl.set_phone_offhook(True)
    ctl.set_phone_reachable(False)
    try:
        ctl.claim_line()
        raise AssertionError("claim should have raised")
    except PhoneUnplugged:
        pass
    await close_stack(ctl, bus, db)


async def test_lease_states_render_over_offhook(tmp_path, unused_tcp_port):
    # during a real call the hook is naturally off; the lease state must win
    _, bus, db, ctl = await make_stack(tmp_path, unused_tcp_port)
    lease = ctl.claim_line()
    ctl.set_phone_offhook(True)
    assert ctl.line_snapshot()["state"] == "held"
    ctl.lease.release(lease)
    assert ctl.line_snapshot()["state"] == "offhook"
    await close_stack(ctl, bus, db)


# -- HTTP surface -------------------------------------------------------------

from aiohttp.test_utils import TestClient, TestServer

from hotline.http import make_app


async def make_http_stack(tmp_path, unused_tcp_port, **env):
    cfg, bus, db, ctl = await make_stack(tmp_path, unused_tcp_port, **env)
    client = TestClient(TestServer(make_app(cfg, ctl)))
    await client.start_server()
    return cfg, bus, db, ctl, client


async def test_claim_409s_offhook(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_http_stack(tmp_path, unused_tcp_port)
    ctl.set_phone_offhook(True)
    resp = await client.post("/call/claim")
    assert resp.status == 409 and (await resp.json())["error"] == "offhook"
    await client.close(); await close_stack(ctl, bus, db)


async def test_line_sim_drives_offhook(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_http_stack(tmp_path, unused_tcp_port)
    assert (await client.post("/admin/line-sim?state=offhook")).status == 401
    resp = await client.post("/admin/line-sim?state=offhook&token=dev-token")
    assert resp.status == 200
    assert ctl.line_snapshot()["state"] == "offhook"
    resp = await client.post("/admin/line-sim?state=clear&token=dev-token")
    assert resp.status == 200
    assert ctl.line_snapshot()["state"] == "idle"
    assert (await client.post(
        "/admin/line-sim?state=bogus&token=dev-token")).status == 400
    await client.close(); await close_stack(ctl, bus, db)


async def test_events_hello_reports_offhook(tmp_path, unused_tcp_port):
    import asyncio as aio
    _, bus, db, ctl, client = await make_http_stack(tmp_path, unused_tcp_port)
    ctl.set_phone_offhook(True)
    ws = await client.ws_connect("/ws/events?feed=rt")
    hello = await aio.wait_for(ws.receive_json(), 5)
    assert hello["type"] == "line_state" and hello["state"] == "offhook"
    await ws.close()
    await client.close(); await close_stack(ctl, bus, db)
