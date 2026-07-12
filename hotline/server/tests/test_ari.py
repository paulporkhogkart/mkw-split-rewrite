from __future__ import annotations

import asyncio
import json

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

    app.router.add_get("/ari/events", events_ws)
    app.router.add_post("/ari/channels", channels)
    app.router.add_post("/ari/channels/externalMedia", external)
    app.router.add_post("/ari/bridges", bridges)
    app.router.add_post("/ari/bridges/{bid}/addChannel", add_channel)
    app.router.add_delete("/ari/channels/{cid}", hangup)
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
