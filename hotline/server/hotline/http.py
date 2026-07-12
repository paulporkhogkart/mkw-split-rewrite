from __future__ import annotations

from aiohttp import web

from .config import Config


async def _healthz(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def make_app(cfg: Config, controller=None) -> web.Application:
    app = web.Application()
    app["cfg"] = cfg
    app["controller"] = controller
    app.router.add_get("/healthz", _healthz)
    return app
