from __future__ import annotations

from aiohttp import web

from .config import Config

CFG_KEY = web.AppKey("cfg", Config)
CONTROLLER_KEY = web.AppKey("controller", object)


async def _healthz(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def make_app(cfg: Config, controller=None) -> web.Application:
    app = web.Application()
    app[CFG_KEY] = cfg
    app[CONTROLLER_KEY] = controller
    app.router.add_get("/healthz", _healthz)
    return app
