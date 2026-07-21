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
from .lease import HELD, ONCALL, RINGING, LineBusy, LineLease
from .recording import CallRecorder, free_space_gib, sweep_retention

MIN_FREE_GIB = 1.0


class PhoneUnplugged(Exception):
    pass


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
        self._call_phone_leg: Optional[PhoneLeg] = None
        self._phone_sess: Optional[AudioSocketSession] = None
        self._reap_task: Optional[asyncio.Task] = None
        self.lease = LineLease(self._publish_line_state,
                               cfg.claim_window_s, cfg.call_backstop_s)
        self.lease.on_expired(self._on_lease_expired)
        self._phone_reachable = True   # echo mode never flips this; real mode
                                       # is driven by the ARI poll (Task 5)
        self._call_lease_id: Optional[str] = None

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        await self._audiosocket.start()
        sweep_retention(self._recordings_root())
        self.bus.publish({"type": "lines_state", "open": False})
        self.bus.publish(self.line_snapshot())

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
            self, send: Callable[[bytes], Awaitable[None]],
            lease_id: Optional[str] = None) -> None:
        if lease_id is not None and not self.lease.valid(lease_id):
            raise KeyError("stale lease")
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

    # -- line lease ----------------------------------------------------------
    def line_snapshot(self) -> dict:
        snap = self.lease.snapshot()
        if snap["state"] == "idle" and not self._phone_reachable:
            snap["state"] = "unplugged"
        return snap

    def _publish_line_state(self, _snap: dict) -> None:
        # always publish the composed view, not the raw lease snapshot
        self.bus.publish(self.line_snapshot())

    def set_phone_reachable(self, ok: bool) -> None:
        if ok == self._phone_reachable:
            return
        self._phone_reachable = ok
        self.bus.publish(self.line_snapshot())

    def claim_line(self) -> str:
        if not self._phone_reachable:
            raise PhoneUnplugged()
        return self.lease.claim()

    async def ring_with_lease(self, lease_id: str) -> str:
        if not self.lease.valid(lease_id):
            raise KeyError("stale lease")
        if self.lease.state != HELD:
            raise RuntimeError("lease not in held state")
        self.lease.mark_ringing(lease_id)
        try:
            call_id = await self.test_ring(self.cfg.call_backstop_s,
                                           caller_label="web",
                                           lease_id=lease_id)
        except Exception:
            self.lease.release(lease_id)
            raise
        self._call_lease_id = lease_id
        return call_id

    async def hangup_with_lease(self, lease_id: str) -> bool:
        if not self.lease.valid(lease_id):
            raise KeyError("stale lease")
        if self._call is not None and self._call_lease_id == lease_id:
            outcome = "completed" if self.lease.state == ONCALL else "dropped"
            await self._call.end(outcome)
            return True
        self.lease.release(lease_id)   # held/ringing with no live call
        return False

    def _on_lease_expired(self, lease_id: str) -> None:
        asyncio.create_task(self._expire_lease(lease_id))

    async def _expire_lease(self, lease_id: str) -> None:
        if self._call is not None and self._call_lease_id == lease_id:
            await self._call.end("dropped")   # reap releases the lease
        else:
            self.lease.release(lease_id)

    def _safe_mark_oncall(self, lease_id: str) -> None:
        try:
            self.lease.mark_oncall(lease_id)
        except KeyError:
            pass   # lease died between answer and mark; reap will clean up

    # -- calls -----------------------------------------------------------------
    async def test_ring(self, seconds: float = 60, caller_label: str = "test",
                        lease_id: Optional[str] = None) -> str:
        if self._caller_send is None:
            raise RuntimeError("no caller connected")
        if self._call is not None:
            raise RuntimeError("call already active")
        if free_space_gib(self._recordings_root()) < MIN_FREE_GIB:
            raise RuntimeError("low disk space")
        call_id = str(uuid.uuid4())
        await asyncio.to_thread(
            self.db.create_call, call_id, caller_label, int(seconds))

        async def send_to_caller(frame: bytes) -> None:
            if self._caller_send:
                await self._caller_send(frame)

        recorder = CallRecorder(self._recordings_root() / call_id)
        holder: list[CallSession] = []
        if self.cfg.echo_mode or self._factory is None:
            phone: PhoneLeg = EchoPhoneLeg(lambda: holder[0])
        else:
            phone = None  # type: ignore  # replaced below
        call = CallSession(call_id=call_id, caller_label=caller_label,
                           seconds=seconds, phone=phone, bus=self.bus,
                           recorder=recorder, send_to_caller=send_to_caller,
                           grace_s=self.cfg.ws_grace_s,
                           on_answered=(
                               (lambda: self._safe_mark_oncall(lease_id))
                               if lease_id else None))
        holder.append(call)
        if phone is None:
            phone = self._factory(call)  # real leg needs the session
            call._phone = phone
        self._call = call
        self._call_phone_leg = phone
        self._reap_task = asyncio.create_task(self._reap(call))
        try:
            await call.start()
        except Exception:
            # a real leg's ring() can raise (e.g. Asterisk down) -- without
            # this the slot wedges: self._call stays set and every future
            # test_ring() 409s forever.
            await call.end("dropped")
            self._call = None
            self._call_phone_leg = None
            self._reap_task = None
            raise
        return call_id

    async def _reap(self, call: CallSession) -> None:
        await call.done.wait()
        await asyncio.to_thread(
            self.db.finish_call, call.call_id, call.outcome or "dropped",
            int(call.seconds_used), f"recordings/{call.call_id}")
        if self._call is call:
            self._call = None
            self._call_phone_leg = None
            if self._call_lease_id is not None:
                self.lease.release(self._call_lease_id)
                self._call_lease_id = None
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
        if call is None or str(sess.uuid) != call.call_id:
            return False
        self._phone_sess = sess
        sess.on_audio(call.on_phone_frame)
        sess.on_closed(call.on_phone_hungup)
        leg = self._call_phone_leg
        if leg is not None and hasattr(leg, "set_audio_sender"):
            leg.set_audio_sender(sess.send_audio)
        return True
