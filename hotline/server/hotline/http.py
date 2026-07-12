from __future__ import annotations

import asyncio
import contextlib
import hmac
from pathlib import Path

import aiohttp
from aiohttp import web

from .config import Config

STATIC_DIR = Path(__file__).parent / "static"

CFG_KEY = web.AppKey("cfg", Config)
CONTROLLER_KEY = web.AppKey("controller", object)
BUS_KEY = web.AppKey("bus", object)


def _authed(request: web.Request) -> bool:
    cfg: Config = request.app[CFG_KEY]
    token = request.query.get("token", "")
    return hmac.compare_digest(token, cfg.admin_token)


async def _healthz(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _test_page(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "test.html")


async def _test_ring(request: web.Request) -> web.Response:
    if not _authed(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    controller = request.app[CONTROLLER_KEY]
    seconds = float(request.query.get("seconds", "60"))
    try:
        call_id = await controller.test_ring(seconds)
    except RuntimeError as e:
        return web.json_response({"error": str(e)}, status=409)
    return web.json_response({"call_id": call_id})


async def _hangup(request: web.Request) -> web.Response:
    if not _authed(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    ok = await request.app[CONTROLLER_KEY].hangup_active()
    return web.json_response({"hungup": ok})


async def _ws_audio(request: web.Request) -> web.WebSocketResponse:
    if not _authed(request):
        raise web.HTTPUnauthorized()
    controller = request.app[CONTROLLER_KEY]
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    async def send(frame: bytes) -> None:
        if not ws.closed:
            await ws.send_bytes(frame)

    try:
        await controller.attach_caller_ws(send)
    except RuntimeError:
        await ws.close(code=4009, message=b"caller slot busy")
        return ws
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.BINARY:
                controller.on_caller_audio(msg.data)
    finally:
        controller.detach_caller_ws()
    return ws


async def _ws_events(request: web.Request) -> web.WebSocketResponse:
    if not _authed(request):
        raise web.HTTPUnauthorized()
    feed = request.query.get("feed", "rt")
    if feed not in ("rt", "delayed"):
        raise web.HTTPBadRequest()
    bus = request.app[BUS_KEY]
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    q = bus.subscribe(feed)
    receive_task = asyncio.create_task(ws.receive())  # resolves on close/any msg
    try:
        while True:
            get_task = asyncio.create_task(q.get())
            done, _ = await asyncio.wait(
                {get_task, receive_task}, return_when=asyncio.FIRST_COMPLETED)
            if get_task in done:
                await ws.send_json(get_task.result())
            else:
                get_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await get_task
            if receive_task in done:
                break  # client closed or spoke — either way we're done
    except (ConnectionError, RuntimeError):
        pass
    finally:
        receive_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await receive_task
        bus.unsubscribe(feed, q)
    return ws


def make_app(cfg: Config, controller=None) -> web.Application:
    app = web.Application()
    app[CFG_KEY] = cfg
    app[CONTROLLER_KEY] = controller
    app[BUS_KEY] = controller.bus if controller else None
    app.router.add_get("/healthz", _healthz)
    app.router.add_get("/test", _test_page)
    app.router.add_static("/static/", STATIC_DIR)
    app.router.add_post("/admin/test-ring", _test_ring)
    app.router.add_post("/admin/hangup", _hangup)
    app.router.add_get("/ws/audio", _ws_audio)
    app.router.add_get("/ws/events", _ws_events)
    return app
