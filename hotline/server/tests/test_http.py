from __future__ import annotations

from aiohttp.test_utils import TestClient, TestServer

from hotline.config import Config
from hotline.http import make_app


async def test_healthz(tmp_path):
    cfg = Config.from_env({"HOTLINE_ENV": "dev", "HOTLINE_DATA_DIR": str(tmp_path)})
    async with TestClient(TestServer(make_app(cfg))) as client:
        resp = await client.get("/healthz")
        assert resp.status == 200
        assert (await resp.json()) == {"ok": True}
