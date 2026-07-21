from __future__ import annotations

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from hotline.config import Config
from hotline.controller import Controller
from hotline.db import Db
from hotline.events import EventBus
from hotline.http import make_app


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
    client = TestClient(TestServer(make_app(cfg, ctl)))
    await client.start_server()
    return cfg, bus, db, ctl, client


async def close_stack(client, ctl, bus, db):
    await client.close(); await ctl.stop(); await bus.stop(); db.close()


async def test_page_served_at_root(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    resp = await client.get("/")
    assert resp.status == 200
    assert "text/html" in resp.headers["Content-Type"]
    await close_stack(client, ctl, bus, db)


async def test_full_public_flow(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    ev = await client.ws_connect("/ws/events?feed=rt")       # no token
    hello = await asyncio.wait_for(ev.receive_json(), 5)
    assert hello["type"] == "line_state" and hello["state"] == "idle"

    lease = (await (await client.post("/call/claim")).json())["lease_id"]
    r2 = await client.post("/call/claim")
    assert r2.status == 409 and (await r2.json())["error"] == "busy"

    ws = await client.ws_connect(f"/ws/audio?lease={lease}")
    resp = await client.post(f"/call/ring?lease={lease}")
    assert resp.status == 200
    assert "call_id" in await resp.json()

    # events feed narrates the lease lifecycle to everyone
    states = set()
    while "oncall" not in states:
        msg = await asyncio.wait_for(ev.receive_json(), 5)
        if msg.get("type") == "line_state":
            states.add(msg["state"])
    assert {"held", "ringing", "oncall"} <= states

    resp = await client.post(f"/call/hangup?lease={lease}")
    assert resp.status == 200 and (await resp.json())["hungup"] is True
    await ws.close(); await ev.close()
    await close_stack(client, ctl, bus, db)


async def test_stale_lease_404s(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    assert (await client.post("/call/ring?lease=deadbeef")).status == 404
    assert (await client.post("/call/hangup?lease=deadbeef")).status == 404
    await close_stack(client, ctl, bus, db)


async def test_audio_ws_rejects_without_credential(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    resp = await client.get("/ws/audio")            # no lease, no token
    assert resp.status == 401
    await close_stack(client, ctl, bus, db)


async def test_bad_origin_rejected_good_origin_allowed(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    bad = {"Origin": "https://evil.example"}
    assert (await client.post("/call/claim", headers=bad)).status == 403
    resp = await client.get("/ws/events?feed=rt", headers=bad)
    assert resp.status == 403
    good = {"Origin": "https://phone.thekartoff.com"}
    assert (await client.post("/call/claim", headers=good)).status == 200
    await close_stack(client, ctl, bus, db)


async def test_delayed_feed_still_needs_token(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    assert (await client.get("/ws/events?feed=delayed")).status == 401
    ws = await client.ws_connect("/ws/events?feed=delayed&token=dev-token")
    await ws.close()
    await close_stack(client, ctl, bus, db)


async def test_unplugged_claim_409s(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    ctl.set_phone_reachable(False)
    resp = await client.post("/call/claim")
    assert resp.status == 409 and (await resp.json())["error"] == "unplugged"
    await close_stack(client, ctl, bus, db)


async def test_page_assets_served(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    for path in ("/", "/static/phone.js", "/static/phone.css",
                 "/static/sfx/ringing.wav", "/static/sfx/hangup.wav",
                 "/static/sfx/ringtone.wav"):
        resp = await client.get(path)
        assert resp.status == 200, path
    body = await (await client.get("/")).text()
    assert "pork phone" in body
    assert "—" not in body          # no em dashes in page copy, ever
    await close_stack(client, ctl, bus, db)
