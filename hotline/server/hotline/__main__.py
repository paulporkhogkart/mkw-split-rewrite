from __future__ import annotations

import asyncio
import contextlib
import logging
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

logger = logging.getLogger(__name__)


class AriPhoneLeg:
    """Real phone leg: rings PJSIP/ata via ARI; audio flows over the
    controller's AudioSocket session (Asterisk externalMedia dials us).
    No-answer is covered by Asterisk's originate timeout (~30 s default):
    the channel dies -> ChannelDestroyed -> on_phone_hungup."""

    def __init__(self, session: CallSession, ari: AriClient,
                 audiosocket_port: int, ring_timeout_s: int = 30) -> None:
        self._session = session
        self._ari = ari
        self._port = audiosocket_port
        self._ring_timeout_s = ring_timeout_s
        self._channel_id: Optional[str] = None
        self._send_audio: Optional[Callable[[bytes], Awaitable[None]]] = None
        self._answered_task: Optional[asyncio.Task] = None
        self._disposed = False
        self._pre_ring_events: list[dict] = []
        ari.on_event(self._on_ari_event)

    def set_audio_sender(self, cb: Callable[[bytes], Awaitable[None]]) -> None:
        self._send_audio = cb

    async def ring(self, caller_name: str) -> None:
        try:
            self._channel_id = await self._ari.originate_phone(
                caller_name, self._session.call_id, self._ring_timeout_s)
        except Exception:
            self._dispose()
            raise
        stash, self._pre_ring_events = self._pre_ring_events, []
        for event in stash:
            self._dispatch(event)

    def _on_ari_event(self, event: dict) -> None:
        if self._disposed:
            return
        if self._channel_id is None:
            self._pre_ring_events.append(event)  # originate still in flight
            return
        self._dispatch(event)

    def _dispatch(self, event: dict) -> None:
        if self._disposed:
            return
        ch = (event.get("channel") or {}).get("id")
        if ch != self._channel_id:
            return
        etype = event.get("type")
        if etype == "StasisStart" and self._answered_task is None:
            self._answered_task = asyncio.create_task(self._on_answered())
        elif etype in ("StasisEnd", "ChannelDestroyed"):
            self._session.on_phone_hungup()
            self._dispose()

    async def _on_answered(self) -> None:
        try:
            em = await self._ari.external_media(
                self._session.call_id, f"127.0.0.1:{self._port}")
            assert self._channel_id is not None
            await self._ari.bridge([self._channel_id, em])
        except Exception:
            logger.exception("answer wiring failed for call %s",
                             self._session.call_id)
            if self._channel_id:
                with contextlib.suppress(Exception):
                    await self._ari.hangup(self._channel_id)
            self._session.on_phone_hungup()  # frees the call slot
            self._dispose()
            return
        self._session.on_phone_answered()

    async def hangup(self) -> None:
        if self._channel_id:
            with contextlib.suppress(Exception):
                await self._ari.hangup(self._channel_id)
        self._session.on_phone_hungup()  # PhoneLeg contract: hangup() ends the call
        self._dispose()

    def _dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._ari.off_event(self._on_ari_event)
        task = self._answered_task
        if task and task is not asyncio.current_task():
            task.cancel()

    async def send_frame(self, frame: bytes) -> None:
        if self._send_audio:
            await self._send_audio(frame)


async def watch_ata(ari: AriClient, controller: Controller,
                    poll_s: float, stop: asyncio.Event) -> None:
    """Poll the ATA endpoint; drive the line's unplugged state."""
    while not stop.is_set():
        try:
            state = await ari.endpoint_state()
        except Exception:
            state = "unknown"
        controller.set_phone_reachable(state == "online")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), poll_s)


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
        ari.on_dead(stop.set)  # blind telephony = shutdown; systemd restarts into lines-closed boot
        await ari.connect()

        def factory(session: CallSession) -> AriPhoneLeg:
            return AriPhoneLeg(session, ari, cfg.audiosocket_port,
                               cfg.ring_timeout_s)

    controller = Controller(cfg, bus, db, phone_leg_factory=factory)
    await controller.start()

    watch_task: Optional[asyncio.Task] = None
    if ari is not None:
        controller.set_phone_reachable(False)   # unknown until first poll answers
        watch_task = asyncio.create_task(
            watch_ata(ari, controller, cfg.ata_poll_s, stop))

    app = make_app(cfg, controller)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", cfg.http_port)
    await site.start()

    await stop.wait()

    await runner.cleanup()
    if watch_task:
        watch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watch_task
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
