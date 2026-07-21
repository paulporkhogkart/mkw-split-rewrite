from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from hotline.ari import AriClient


def make_fake_ari(record: dict):
    app = web.Application()

    async def events_ws(request: web.Request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        record["ws_query"] = dict(request.query)
        await ws.send_str(json.dumps({"type": "StasisStart",
                                      "channel": {"id": "ch-1"}}))
        async for _ in ws:
            pass
        return ws

    async def channels(request: web.Request):
        record["originate"] = await request.json()
        return web.json_response({"id": "ch-1"})

    async def external(request: web.Request):
        record["external"] = await request.json()
        return web.json_response({"id": "ch-em"})

    async def bridges(request: web.Request):
        return web.json_response({"id": "br-1"})

    async def add_channel(request: web.Request):
        record["bridged"] = request.query["channel"]
        return web.Response(status=204)

    async def hangup(request: web.Request):
        record["hungup"] = request.match_info["cid"]
        return web.Response(status=204)

    async def endpoint(request: web.Request):
        record["endpoint_asked"] = (request.match_info["tech"],
                                    request.match_info["res"])
        return web.json_response({"technology": "PJSIP", "resource": "ata",
                                  "state": record.get("endpoint_state", "online")})

    app.router.add_get("/ari/events", events_ws)
    app.router.add_post("/ari/channels", channels)
    app.router.add_post("/ari/channels/externalMedia", external)
    app.router.add_post("/ari/bridges", bridges)
    app.router.add_post("/ari/bridges/{bid}/addChannel", add_channel)
    app.router.add_delete("/ari/channels/{cid}", hangup)
    app.router.add_get("/ari/endpoints/{tech}/{res}", endpoint)
    return app


async def test_ari_flow():
    record: dict = {}
    server = TestServer(make_fake_ari(record))
    await server.start_server()
    base = f"http://127.0.0.1:{server.port}"
    events: list[dict] = []

    client = AriClient(base, "hotline", "pw")
    client.on_event(events.append)
    await client.connect()
    await asyncio.sleep(0.05)
    assert record["ws_query"]["app"] == "pork"
    assert events and events[0]["type"] == "StasisStart"

    ch = await client.originate_phone("PORK FAN", "u-1")
    assert ch == "ch-1"
    assert record["originate"]["endpoint"] == "PJSIP/ata"
    assert record["originate"]["variables"]["PORK_UUID"] == "u-1"

    em = await client.external_media("u-1", "127.0.0.1:9101")
    assert em == "ch-em"
    assert record["external"]["encapsulation"] == "audiosocket"
    assert record["external"]["data"] == "u-1"

    br = await client.bridge(["ch-1", "ch-em"])
    assert br == "br-1" and record["bridged"] == "ch-1,ch-em"

    await client.hangup("ch-1")
    assert record["hungup"] == "ch-1"
    await client.close()
    await server.close()


async def test_listener_exception_does_not_kill_pipe():
    app = web.Application()

    async def events_ws(request: web.Request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str(json.dumps({"type": "one"}))
        await ws.send_str(json.dumps({"type": "two"}))
        async for _ in ws:
            pass
        return ws

    app.router.add_get("/ari/events", events_ws)
    server = TestServer(app)
    await server.start_server()
    got: list[str] = []

    def bad(_e: dict) -> None:
        raise RuntimeError("boom")

    client = AriClient(f"http://127.0.0.1:{server.port}", "u", "p")
    client.on_event(bad)
    client.on_event(lambda e: got.append(e["type"]))
    await client.connect()
    await asyncio.sleep(0.1)
    assert got == ["one", "two"]  # second listener unaffected, pipe alive
    await client.close()
    await server.close()


async def test_off_event_removes_listener():
    client = AriClient("http://127.0.0.1:1", "u", "p")

    def cb(_e: dict) -> None:
        return None

    client.on_event(cb)
    client.off_event(cb)
    client.off_event(cb)  # idempotent
    assert client._listeners == []


async def test_on_dead_fires_when_remote_closes():
    app = web.Application()

    async def events_ws(request: web.Request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.close()  # remote closes immediately
        return ws

    app.router.add_get("/ari/events", events_ws)
    server = TestServer(app)
    await server.start_server()
    died = asyncio.Event()
    client = AriClient(f"http://127.0.0.1:{server.port}", "u", "p")
    client.on_dead(died.set)
    await client.connect()
    await asyncio.wait_for(died.wait(), 2)
    await client.close()
    await server.close()


async def test_bridge_cleans_up_on_addchannel_failure():
    record: dict = {}
    app = web.Application()

    async def bridges(_request: web.Request):
        return web.json_response({"id": "br-9"})

    async def add_channel(_request: web.Request):
        return web.Response(status=500)

    async def delete_bridge(request: web.Request):
        record["deleted"] = request.match_info["bid"]
        return web.Response(status=204)

    async def events_ws(request: web.Request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for _ in ws:
            pass
        return ws

    app.router.add_get("/ari/events", events_ws)
    app.router.add_post("/ari/bridges", bridges)
    app.router.add_post("/ari/bridges/{bid}/addChannel", add_channel)
    app.router.add_delete("/ari/bridges/{bid}", delete_bridge)
    server = TestServer(app)
    await server.start_server()

    client = AriClient(f"http://127.0.0.1:{server.port}", "u", "p")
    await client.connect()
    with pytest.raises(aiohttp.ClientResponseError):
        await client.bridge(["ch-1", "ch-em"])
    assert record["deleted"] == "br-9"
    await client.close()
    await server.close()


async def test_endpoint_state():
    record: dict = {}
    server = TestServer(make_fake_ari(record))
    await server.start_server()
    client = AriClient(f"http://127.0.0.1:{server.port}", "hotline", "pw")
    await client.connect()
    assert await client.endpoint_state() == "online"
    assert record["endpoint_asked"] == ("PJSIP", "ata")
    record["endpoint_state"] = "offline"
    assert await client.endpoint_state() == "offline"
    await client.close()
    await server.close()


async def test_endpoint_state_unknown_on_error():
    # no /ari/endpoints route at all -> 404 -> "unknown", no raise
    app = web.Application()

    async def events_ws(request: web.Request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for _ in ws:
            pass
        return ws

    app.router.add_get("/ari/events", events_ws)
    server = TestServer(app)
    await server.start_server()
    client = AriClient(f"http://127.0.0.1:{server.port}", "u", "p")
    await client.connect()
    assert await client.endpoint_state() == "unknown"
    await client.close()
    await server.close()


async def test_originate_sends_timeout():
    record: dict = {}
    server = TestServer(make_fake_ari(record))
    await server.start_server()
    client = AriClient(f"http://127.0.0.1:{server.port}", "hotline", "pw")
    await client.connect()
    await client.originate_phone("web", "u-9", timeout_s=30)
    assert record["originate"]["timeout"] == 30
    await client.close()
    await server.close()
