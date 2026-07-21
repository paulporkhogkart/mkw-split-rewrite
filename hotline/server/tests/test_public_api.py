from __future__ import annotations

import asyncio

import aiohttp
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
                 "/static/sfx/ringback.wav", "/static/sfx/busy.wav",
                 "/static/sfx/dialtone.wav", "/static/sfx/clunk.wav"):
        resp = await client.get(path)
        assert resp.status == 200, path
    body = await (await client.get("/")).text()
    assert "pork phone" in body
    assert "—" not in body          # no em dashes in page copy, ever
    await close_stack(client, ctl, bus, db)


async def test_expired_lease_kicks_attached_ws_and_line_recovers(tmp_path, unused_tcp_port):
    # a WS attached with a lease that then expires (claim window, no ring)
    # must not brick the line: the server should force-close it, and a
    # fresh caller must be able to claim + attach + ring right after.
    _, bus, db, ctl, client = await make_stack(
        tmp_path, unused_tcp_port, HOTLINE_CLAIM_WINDOW_S="0.05")
    lease = (await (await client.post("/call/claim")).json())["lease_id"]
    ws = await client.ws_connect(f"/ws/audio?lease={lease}")

    msg = await asyncio.wait_for(ws.receive(), 5)
    assert msg.type == aiohttp.WSMsgType.CLOSE
    assert msg.data == 4008
    assert ws.closed

    lease2 = (await (await client.post("/call/claim")).json())["lease_id"]
    ws2 = await client.ws_connect(f"/ws/audio?lease={lease2}")
    resp = await client.post(f"/call/ring?lease={lease2}")
    assert resp.status == 200
    assert "call_id" in await resp.json()
    await ws2.close()
    await close_stack(client, ctl, bus, db)


async def test_ring_rejected_when_attached_ws_bound_to_other_lease(tmp_path, unused_tcp_port):
    # A attaches its caller WS with lease A. Lease A gets released out from
    # under the WS (without the WS itself having been kicked yet -- e.g. a
    # kick still in flight) and a second client claims the now-free line as
    # lease B. Ringing with B must not hijack A's still-attached WS as the
    # caller leg of B's call; it must 409.
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    lease_a = (await (await client.post("/call/claim")).json())["lease_id"]
    ws = await client.ws_connect(f"/ws/audio?lease={lease_a}")
    await asyncio.sleep(0.05)   # let the attach land

    ctl.lease.release(lease_a)   # simulate a release that raced ahead of the kick

    lease_b = (await (await client.post("/call/claim")).json())["lease_id"]
    resp = await client.post(f"/call/ring?lease={lease_b}")
    assert resp.status == 409

    await ws.close()
    await close_stack(client, ctl, bus, db)
