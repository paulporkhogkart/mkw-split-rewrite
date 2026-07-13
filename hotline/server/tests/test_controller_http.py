from __future__ import annotations

import asyncio
import re

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

from hotline import audio
from hotline.config import Config
from hotline.controller import Controller
from hotline.db import Db
from hotline.events import EventBus
from hotline.http import make_app


async def make_stack(tmp_path, unused_tcp_port, echo=True):
    cfg = Config.from_env({
        "HOTLINE_ENV": "dev", "HOTLINE_DATA_DIR": str(tmp_path),
        "HOTLINE_AUDIOSOCKET_PORT": str(unused_tcp_port),
        "HOTLINE_ECHO": "1" if echo else "",
    })
    bus = EventBus(delay_n=0.05)
    await bus.start()
    db = Db(tmp_path / "h.db"); db.init()
    ctl = Controller(cfg, bus, db, phone_leg_factory=None)
    await ctl.start()
    client = TestClient(TestServer(make_app(cfg, ctl)))
    await client.start_server()
    return cfg, bus, db, ctl, client


async def test_auth_rejected(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    resp = await client.post("/admin/test-ring?token=wrong")
    assert resp.status == 401
    await client.close(); await ctl.stop(); await bus.stop(); db.close()


async def test_ring_requires_caller(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    resp = await client.post("/admin/test-ring?token=dev-token")
    assert resp.status == 409  # no caller connected
    await client.close(); await ctl.stop(); await bus.stop(); db.close()


async def test_echo_call_roundtrip(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    ws = await client.ws_connect("/ws/audio?token=dev-token")
    ev = await client.ws_connect("/ws/events?feed=rt&token=dev-token")

    resp = await client.post("/admin/test-ring?token=dev-token&seconds=1")
    assert resp.status == 200
    call_id = (await resp.json())["call_id"]
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", call_id)

    # stream 500 ms of tone; echo leg loops it straight back
    frames = audio.tone_frames(440.0, 500)
    got = bytearray()

    async def sender():
        for f in frames:
            await ws.send_bytes(f)
            await asyncio.sleep(0.02)

    async def receiver():
        while len(got) < 10 * audio.FRAME_BYTES:
            msg = await asyncio.wait_for(ws.receive(), 5)
            if msg.type == aiohttp.WSMsgType.BINARY:
                got.extend(msg.data)

    await asyncio.gather(sender(), receiver())
    assert len(got) >= 10 * audio.FRAME_BYTES  # audio came back

    # events observed
    seen = set()
    while len(seen) < 2:
        msg = await asyncio.wait_for(ev.receive_json(), 5)
        seen.add(msg["type"])
    assert {"call_ringing", "call_active"} <= seen

    # timer (1 s) expires the call; DB row completed
    await asyncio.sleep(2.0)
    row = db._conn.execute(
        "SELECT outcome FROM calls WHERE call_id=?", (call_id,)).fetchone()
    assert row and row[0] == "completed"

    await ws.close(); await ev.close()
    await client.close(); await ctl.stop(); await bus.stop(); db.close()


async def test_events_ws_unsubscribes_on_idle_disconnect(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    ev = await client.ws_connect("/ws/events?feed=rt&token=dev-token")
    await asyncio.sleep(0.05)
    assert len(bus._subs["rt"]) == 1
    await ev.close()  # idle disconnect: no events flowing
    await asyncio.sleep(0.2)
    assert len(bus._subs["rt"]) == 0  # handler exited, subscription gone
    await client.close(); await ctl.stop(); await bus.stop(); db.close()


async def test_stop_joins_reap_before_returning(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    ws = await client.ws_connect("/ws/audio?token=dev-token")
    resp = await client.post("/admin/test-ring?token=dev-token&seconds=30")
    call_id = (await resp.json())["call_id"]
    await asyncio.sleep(0.1)  # call active
    await ctl.stop()  # must not return until _reap finalized the row
    row = db._conn.execute(
        "SELECT outcome FROM calls WHERE call_id=?", (call_id,)).fetchone()
    assert row and row[0] == "dropped"
    await ws.close(); await client.close(); await bus.stop(); db.close()


async def test_ws_audio_drops_missized_frames(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    ws = await client.ws_connect("/ws/audio?token=dev-token")
    resp = await client.post("/admin/test-ring?token=dev-token&seconds=2")
    assert resp.status == 200
    await ws.send_bytes(b"\x01\x02\x03")            # garbage: dropped, no crash
    await ws.send_bytes(b"\x00" * 640)              # oversized: dropped
    await ws.send_bytes(audio.SILENCE_FRAME)        # valid: accepted
    await asyncio.sleep(0.3)                        # pump still alive
    assert (await client.post("/admin/hangup?token=dev-token")).status == 200
    await ws.close(); await client.close(); await ctl.stop(); await bus.stop(); db.close()
