from __future__ import annotations

import asyncio
import contextlib
import hmac
from pathlib import Path

import aiohttp
from aiohttp import web

from . import audio
from .config import Config
from .controller import PhoneUnplugged
from .lease import LineBusy

STATIC_DIR = Path(__file__).parent / "static"

CFG_KEY = web.AppKey("cfg", Config)
CONTROLLER_KEY = web.AppKey("controller", object)
BUS_KEY = web.AppKey("bus", object)


def _authed(request: web.Request) -> bool:
    cfg: Config = request.app[CFG_KEY]
    token = request.query.get("token", "")
    return hmac.compare_digest(token, cfg.admin_token)


def _origin_ok(request: web.Request) -> bool:
    origin = request.headers.get("Origin")
    if origin is None:
        return True   # non-browser clients; the URL is the gate this phase
    return origin in request.app[CFG_KEY].allowed_origins


async def _healthz(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def _test_page(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "test.html")


async def _call_claim(request: web.Request) -> web.Response:
    if not _origin_ok(request):
        return web.json_response({"error": "forbidden"}, status=403)
    controller = request.app[CONTROLLER_KEY]
    try:
        lease_id = controller.claim_line()
    except LineBusy:
        return web.json_response({"error": "busy"}, status=409)
    except PhoneUnplugged:
        return web.json_response({"error": "unplugged"}, status=409)
    return web.json_response({"lease_id": lease_id})


async def _call_ring(request: web.Request) -> web.Response:
    if not _origin_ok(request):
        return web.json_response({"error": "forbidden"}, status=403)
    controller = request.app[CONTROLLER_KEY]
    lease = request.query.get("lease", "")
    try:
        call_id = await controller.ring_with_lease(lease)
    except KeyError:
        return web.json_response({"error": "stale_lease"}, status=404)
    except RuntimeError as e:
        return web.json_response({"error": str(e)}, status=409)
    return web.json_response({"call_id": call_id})


async def _call_hangup(request: web.Request) -> web.Response:
    if not _origin_ok(request):
        return web.json_response({"error": "forbidden"}, status=403)
    controller = request.app[CONTROLLER_KEY]
    lease = request.query.get("lease", "")
    try:
        hungup = await controller.hangup_with_lease(lease)
    except KeyError:
        return web.json_response({"error": "stale_lease"}, status=404)
    return web.json_response({"hungup": hungup})


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
    if not _origin_ok(request):
        raise web.HTTPForbidden()
    controller = request.app[CONTROLLER_KEY]
    lease = request.query.get("lease", "")
    lease_ok = lease and controller.lease.valid(lease)
    if not lease_ok and not _authed(request):
        raise web.HTTPUnauthorized()
    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(request)

    async def send(frame: bytes) -> None:
        if not ws.closed:
            await ws.send_bytes(frame)

    def kick() -> None:
        # fires when this WS's lease is released out from under it (claim-window
        # expiry, backstop, ring failure, or normal hangup) so a stale WS never
        # keeps holding the one caller slot; bench (token, no-lease) connections
        # are never kicked since they're never attached with a lease.
        if not ws.closed:
            asyncio.ensure_future(ws.close(code=4008, message=b"lease expired"))

    try:
        await controller.attach_caller_ws(
            send, lease_id=lease if lease_ok else None,
            kick=kick if lease_ok else None)
    except RuntimeError:
        await ws.close(code=4009, message=b"caller slot busy")
        return ws
    except KeyError:
        await ws.close(code=4008, message=b"lease expired")
        return ws
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.BINARY:
                if len(msg.data) == audio.FRAME_BYTES:
                    controller.on_caller_audio(msg.data)
    finally:
        controller.detach_caller_ws()
    return ws


async def _ws_events(request: web.Request) -> web.WebSocketResponse:
    if not _origin_ok(request):
        raise web.HTTPForbidden()
    feed = request.query.get("feed", "rt")
    if feed not in ("rt", "delayed"):
        raise web.HTTPBadRequest()
    if feed == "delayed" and not _authed(request):
        raise web.HTTPUnauthorized()
    bus = request.app[BUS_KEY]
    controller = request.app[CONTROLLER_KEY]
    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(request)
    q = bus.subscribe(feed)
    try:
        await ws.send_json(controller.line_snapshot())   # hello
    except ConnectionError:
        bus.unsubscribe(feed, q)
        return ws
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
    app.router.add_get("/", _index)
    app.router.add_get("/test", _test_page)
    app.router.add_static("/static/", STATIC_DIR)
    app.router.add_post("/admin/test-ring", _test_ring)
    app.router.add_post("/admin/hangup", _hangup)
    app.router.add_post("/call/claim", _call_claim)
    app.router.add_post("/call/ring", _call_ring)
    app.router.add_post("/call/hangup", _call_hangup)
    app.router.add_get("/ws/audio", _ws_audio)
    app.router.add_get("/ws/events", _ws_events)
    return app
