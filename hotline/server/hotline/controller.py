from __future__ import annotations

import asyncio
import contextlib
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .audiosocket import AudioSocketServer, AudioSocketSession
from .call import CallSession, PhoneLeg
from .config import Config
from .db import Db
from .events import EventBus
from .recording import CallRecorder, free_space_gib, sweep_retention

MIN_FREE_GIB = 1.0


class EchoPhoneLeg:
    """Dev/e2e phone: answers instantly, loops audio back."""

    def __init__(self, session_getter: Callable[[], CallSession]) -> None:
        self._get = session_getter

    async def ring(self, caller_name: str) -> None:
        self._get().on_phone_answered()

    async def hangup(self) -> None:
        self._get().on_phone_hungup()

    async def send_frame(self, frame: bytes) -> None:
        self._get().on_phone_frame(frame)


class Controller:
    def __init__(self, cfg: Config, bus: EventBus, db: Db,
                 phone_leg_factory: Optional[Callable[[CallSession], PhoneLeg]],
                 ) -> None:
        self.cfg = cfg
        self.bus = bus
        self.db = db
        self._factory = phone_leg_factory
        self._audiosocket = AudioSocketServer(cfg.audiosocket_port,
                                              self._on_audiosocket_session)
        self._caller_send: Optional[Callable[[bytes], Awaitable[None]]] = None
        self._call: Optional[CallSession] = None
        self._phone_sess: Optional[AudioSocketSession] = None
        self._reap_task: Optional[asyncio.Task] = None

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        await self._audiosocket.start()
        sweep_retention(self._recordings_root())
        self.bus.publish({"type": "lines_state", "open": False})

    async def stop(self) -> None:
        if self._call:
            await self._call.end("dropped")
        if self._reap_task:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._reap_task, 10)
        await self._audiosocket.stop()

    def _recordings_root(self) -> Path:
        return self.cfg.data_dir / "recordings"

    # -- caller WS -----------------------------------------------------------
    async def attach_caller_ws(
            self, send: Callable[[bytes], Awaitable[None]]) -> None:
        if self._caller_send is not None:
            raise RuntimeError("caller slot busy")
        self._caller_send = send
        if self._call is not None:
            self._call.on_caller_recovered()

    def detach_caller_ws(self) -> None:
        self._caller_send = None
        if self._call:
            self._call.on_caller_lost()

    def on_caller_audio(self, frame: bytes) -> None:
        if self._call:
            self._call.on_caller_frame(frame)

    # -- calls -----------------------------------------------------------------
    async def test_ring(self, seconds: float = 60) -> str:
        if self._caller_send is None:
            raise RuntimeError("no caller connected")
        if self._call is not None:
            raise RuntimeError("call already active")
        if free_space_gib(self._recordings_root()) < MIN_FREE_GIB:
            raise RuntimeError("low disk space")
        call_id = uuid.uuid4().hex
        await asyncio.to_thread(
            self.db.create_call, call_id, "test", int(seconds))

        async def send_to_caller(frame: bytes) -> None:
            if self._caller_send:
                await self._caller_send(frame)

        recorder = CallRecorder(self._recordings_root() / call_id)
        holder: list[CallSession] = []
        if self.cfg.echo_mode or self._factory is None:
            phone: PhoneLeg = EchoPhoneLeg(lambda: holder[0])
        else:
            phone = None  # type: ignore  # replaced below
        call = CallSession(call_id=call_id, caller_label="test",
                           seconds=seconds, phone=phone, bus=self.bus,
                           recorder=recorder, send_to_caller=send_to_caller)
        holder.append(call)
        if phone is None:
            call._phone = self._factory(call)  # real leg needs the session
        self._call = call
        self._reap_task = asyncio.create_task(self._reap(call))
        await call.start()
        return call_id

    async def _reap(self, call: CallSession) -> None:
        await call.done.wait()
        await asyncio.to_thread(
            self.db.finish_call, call.call_id, call.outcome or "dropped",
            int(call.seconds_used), f"recordings/{call.call_id}")
        if self._call is call:
            self._call = None
        if self._phone_sess:
            await self._phone_sess.terminate()
            self._phone_sess = None

    async def hangup_active(self) -> bool:
        if not self._call:
            return False
        await self._call.end("completed")
        return True

    # -- AudioSocket (Asterisk dials in with the call uuid) --------------------
    async def _on_audiosocket_session(self, sess: AudioSocketSession) -> bool:
        call = self._call
        if call is None or sess.uuid.hex != call.call_id:
            return False
        self._phone_sess = sess
        sess.on_audio(call.on_phone_frame)
        sess.on_closed(call.on_phone_hungup)
        return True
