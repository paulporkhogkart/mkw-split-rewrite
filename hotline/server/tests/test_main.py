from __future__ import annotations

import asyncio

import aiohttp

from hotline.__main__ import build_and_run
from hotline.config import Config


async def test_boots_serves_and_stops(tmp_path, unused_tcp_port_factory):
    http_port = unused_tcp_port_factory()
    cfg = Config.from_env({
        "HOTLINE_ENV": "dev", "HOTLINE_DATA_DIR": str(tmp_path),
        "HOTLINE_HTTP_PORT": str(http_port),
        "HOTLINE_AUDIOSOCKET_PORT": str(unused_tcp_port_factory()),
        "HOTLINE_ECHO": "1",
    })
    stop = asyncio.Event()
    task = asyncio.create_task(build_and_run(cfg, stop))
    await asyncio.sleep(0.3)
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{http_port}/healthz") as resp:
            assert resp.status == 200
    stop.set()
    await asyncio.wait_for(task, 5)
