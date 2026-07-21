from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Callable, Optional

import aiohttp

logger = logging.getLogger(__name__)


class AriClient:
    def __init__(self, base_url: str, user: str, password: str,
                 app: str = "pork") -> None:
        self._base = base_url.rstrip("/")
        self._auth_header = {"Authorization": aiohttp.encode_basic_auth(user, password)}
        self._api_key = f"{user}:{password}"
        self.app = app
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._listeners: list[Callable[[dict], None]] = []
        self._on_dead: Optional[Callable[[], None]] = None

    def on_event(self, cb: Callable[[dict], None]) -> None:
        self._listeners.append(cb)

    def off_event(self, cb: Callable[[dict], None]) -> None:
        with contextlib.suppress(ValueError):
            self._listeners.remove(cb)

    def on_dead(self, cb: Callable[[], None]) -> None:
        """Called when the events WS dies without close() — the client is blind."""
        self._on_dead = cb

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession(headers=self._auth_header)
        url = f"{self._base}/ari/events?app={self.app}&api_key={self._api_key}"
        ws = await self._session.ws_connect(url)
        self._ws_task = asyncio.create_task(self._listen(ws))

    async def _listen(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    event = json.loads(msg.data)
                except ValueError:
                    logger.warning("ari: non-JSON event frame dropped")
                    continue
                for cb in self._listeners:
                    try:
                        cb(event)
                    except Exception:
                        logger.exception("ari: event listener failed")
        except asyncio.CancelledError:
            raise
        logger.warning("ari: events websocket closed by remote")
        if self._on_dead:
            self._on_dead()

    async def close(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ws_task
        if self._session:
            await self._session.close()

    async def _post(self, path: str, payload: dict) -> dict:
        assert self._session is not None
        async with self._session.post(self._base + path, json=payload) as resp:
            resp.raise_for_status()
            return await resp.json() if resp.status != 204 else {}

    async def originate_phone(self, caller_id: str, channel_var_uuid: str,
                              timeout_s: int = 30) -> str:
        data = await self._post("/ari/channels", {
            "endpoint": "PJSIP/ata", "app": self.app, "callerId": caller_id,
            "timeout": timeout_s, "appArgs": "phone",
            "variables": {"PORK_UUID": channel_var_uuid}})
        return data["id"]

    async def endpoint_state(self, tech: str = "PJSIP",
                             resource: str = "ata") -> str:
        assert self._session is not None
        async with self._session.get(
                f"{self._base}/ari/endpoints/{tech}/{resource}") as resp:
            if resp.status != 200:
                return "unknown"
            data = await resp.json()
            return data.get("state", "unknown")

    async def external_media(self, audiosocket_uuid: str, host: str) -> str:
        data = await self._post("/ari/channels/externalMedia", {
            "app": self.app, "external_host": host,
            "encapsulation": "audiosocket", "transport": "tcp",
            "format": "slin", "data": audiosocket_uuid})
        return data["id"]

    async def bridge(self, channel_ids: list[str]) -> str:
        data = await self._post("/ari/bridges", {"type": "mixing"})
        bridge_id = data["id"]
        assert self._session is not None
        try:
            async with self._session.post(
                f"{self._base}/ari/bridges/{bridge_id}/addChannel",
                params={"channel": ",".join(channel_ids)},
            ) as resp:
                resp.raise_for_status()
        except Exception:
            with contextlib.suppress(Exception):
                async with self._session.delete(
                        f"{self._base}/ari/bridges/{bridge_id}"):
                    pass
            raise
        return bridge_id

    async def hangup(self, channel_id: str) -> None:
        assert self._session is not None
        async with self._session.delete(
            f"{self._base}/ari/channels/{channel_id}"
        ) as resp:
            if resp.status not in (204, 404):
                resp.raise_for_status()
