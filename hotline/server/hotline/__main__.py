from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from typing import Awaitable, Callable, Optional

from aiohttp import web

from .ari import AriClient
from .call import CallSession
from .config import Config
from .controller import Controller
from .db import Db
from .events import EventBus
from .http import make_app


class AriPhoneLeg:
    """Real phone leg: rings PJSIP/ata via ARI; audio flows over the
    controller's AudioSocket session (Asterisk externalMedia dials us)."""

    def __init__(self, session: CallSession, ari: AriClient,
                 audiosocket_port: int) -> None:
        self._session = session
        self._ari = ari
        self._port = audiosocket_port
        self._channel_id: Optional[str] = None
        self._send_audio: Optional[Callable[[bytes], Awaitable[None]]] = None
        ari.on_event(self._on_ari_event)

    def set_audio_sender(self, cb: Callable[[bytes], Awaitable[None]]) -> None:
        self._send_audio = cb

    async def ring(self, caller_name: str) -> None:
        self._channel_id = await self._ari.originate_phone(
            caller_name, self._session.call_id)

    def _on_ari_event(self, event: dict) -> None:
        ch = (event.get("channel") or {}).get("id")
        if ch != self._channel_id:
            return
        if event.get("type") == "StasisStart":
            asyncio.create_task(self._on_answered())
        elif event.get("type") in ("StasisEnd", "ChannelDestroyed"):
            self._session.on_phone_hungup()

    async def _on_answered(self) -> None:
        em = await self._ari.external_media(
            self._session.call_id, f"127.0.0.1:{self._port}")
        assert self._channel_id is not None
        await self._ari.bridge([self._channel_id, em])
        self._session.on_phone_answered()

    async def hangup(self) -> None:
        if self._channel_id:
            with contextlib.suppress(Exception):
                await self._ari.hangup(self._channel_id)

    async def send_frame(self, frame: bytes) -> None:
        if self._send_audio:
            await self._send_audio(frame)


async def build_and_run(cfg: Config, stop: asyncio.Event) -> None:
    db = Db(cfg.data_dir / "hotline.db")
    db.init()
    delay_n = float(db.get_setting("delay_n", str(cfg.delay_n)))
    bus = EventBus(delay_n=delay_n)
    await bus.start()

    ari: Optional[AriClient] = None
    factory = None
    if not cfg.echo_mode:
        ari = AriClient(cfg.ari_url, cfg.ari_user, cfg.ari_password)
        await ari.connect()

        def factory(session: CallSession) -> AriPhoneLeg:
            return AriPhoneLeg(session, ari, cfg.audiosocket_port)

    controller = Controller(cfg, bus, db, phone_leg_factory=factory)
    await controller.start()

    app = make_app(cfg, controller)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", cfg.http_port)
    await site.start()

    await stop.wait()

    await runner.cleanup()
    await controller.stop()
    await bus.stop()
    if ari:
        await ari.close()
    db.close()


def main() -> None:
    cfg = Config.from_env(os.environ)

    async def run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):  # Windows
                loop.add_signal_handler(sig, stop.set)
        await build_and_run(cfg, stop)

    asyncio.run(run())


if __name__ == "__main__":
    main()
