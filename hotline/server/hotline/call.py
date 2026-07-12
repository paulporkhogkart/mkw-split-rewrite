from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Awaitable, Callable, Optional, Protocol

from . import audio
from .events import EventBus
from .jitter import JitterBuffer
from .recording import CallRecorder

logger = logging.getLogger(__name__)

WARNING_S = 10.0
BEEP = audio.tone_frames(440.0, 200)
TIMES_UP = audio.tone_frames(300.0, 400)

# If no phone frame arrived within this window, the pump delivers caller-bound
# tones itself (phone audio normally ticks every 20 ms and carries the tone mix).
CALLER_TONE_FALLBACK_S = 0.04


class PhoneLeg(Protocol):
    async def ring(self, caller_name: str) -> None: ...
    async def hangup(self) -> None: ...
    async def send_frame(self, frame: bytes) -> None: ...


class CallSession:
    def __init__(self, call_id: str, caller_label: str, seconds: float,
                 phone: PhoneLeg, bus: EventBus, recorder: CallRecorder,
                 send_to_caller: Callable[[bytes], Awaitable[None]],
                 grace_s: float = 10.0) -> None:
        self.call_id = call_id
        self.caller_label = caller_label
        self.seconds = seconds
        self.outcome: Optional[str] = None
        self.seconds_used = 0.0
        self.done = asyncio.Event()
        self._phone = phone
        self._bus = bus
        self._recorder = recorder
        self._send_to_caller = send_to_caller
        self._grace_s = grace_s
        self._jitter = JitterBuffer()
        self._inject_phone: list[bytes] = []    # tones bound for the phone (mixed in pump)
        self._inject_caller: list[bytes] = []   # tones bound for the caller (mixed on forward)
        self._answered_at: Optional[float] = None
        self._warned = False
        self._expired = False
        self._last_phone_frame = 0.0
        self._pump_task: Optional[asyncio.Task] = None
        self._grace_task: Optional[asyncio.Task] = None
        self._side_tasks: set[asyncio.Task] = set()
        self._ending = False

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        self._bus.publish({"type": "call_ringing", "call_id": self.call_id,
                           "caller": self.caller_label})
        await self._phone.ring(self.caller_label)

    def on_phone_answered(self) -> None:
        if self._answered_at is not None or self._ending:
            return
        self._answered_at = time.monotonic()
        self._bus.publish({"type": "call_active", "call_id": self.call_id,
                           "caller": self.caller_label, "seconds": self.seconds})
        self._pump_task = asyncio.create_task(self._pump())

    def on_phone_hungup(self) -> None:
        if self._ending:
            return
        self._spawn(self.end(
            "completed" if self._answered_at is not None else "dropped"))

    def on_caller_frame(self, frame: bytes) -> None:
        self._jitter.push(frame)

    def on_caller_lost(self) -> None:
        if self._grace_task is None and not self._ending:
            self._grace_task = asyncio.create_task(self._grace())

    def on_caller_recovered(self) -> None:
        if self._grace_task is not None and not self._ending:
            self._grace_task.cancel()
            self._grace_task = None

    async def _grace(self) -> None:
        await asyncio.sleep(self._grace_s)
        await self.end("dropped")

    def on_phone_frame(self, frame: bytes) -> None:
        if self._ending:
            return
        self._recorder.add_phone(frame)          # raw phone leg, pre-mix
        self._last_phone_frame = time.monotonic()
        out = frame
        if self._inject_caller:
            out = audio.mix_frames(frame, self._inject_caller.pop(0))
        self._spawn(self._send_to_caller(out))

    # -- helpers -------------------------------------------------------------
    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.create_task(coro)
        self._side_tasks.add(task)
        task.add_done_callback(self._side_tasks.discard)

    def _queue_tones(self, frames: list[bytes]) -> None:
        self._inject_phone.extend(frames)
        self._inject_caller.extend(frames)

    # -- 20 ms pump: caller->phone, timer, tone injection ----------------------
    async def _pump(self) -> None:
        next_t = time.monotonic()
        try:
            while not self._ending:
                frame = self._jitter.pull()
                self._recorder.add_caller(frame)  # raw caller leg, pre-mix
                if self._inject_phone:
                    frame = audio.mix_frames(frame, self._inject_phone.pop(0))
                await self._phone.send_frame(frame)

                now = time.monotonic()
                if (self._inject_caller
                        and now - self._last_phone_frame > CALLER_TONE_FALLBACK_S):
                    await self._send_to_caller(self._inject_caller.pop(0))

                elapsed = now - self._answered_at
                self.seconds_used = elapsed
                remaining = self.seconds - elapsed
                if not self._warned and remaining <= min(WARNING_S, self.seconds):
                    self._warned = True
                    self._bus.publish({"type": "call_warning",
                                       "call_id": self.call_id})
                    self._queue_tones(BEEP + BEEP)
                if remaining <= 0 and not self._expired:
                    self._expired = True
                    self._queue_tones(TIMES_UP)   # drains paced through this loop
                if self._expired and not self._inject_phone:
                    await self._phone.hangup()
                    return

                next_t += audio.FRAME_MS / 1000
                await asyncio.sleep(max(0.0, next_t - time.monotonic()))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("pump failed for call %s", self.call_id)
            await self.end("dropped")

    async def end(self, outcome: str) -> None:
        if self._ending:
            return
        self._ending = True
        self.outcome = outcome
        try:
            for task in (self._pump_task, self._grace_task):
                if task and task is not asyncio.current_task():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            for task in list(self._side_tasks):
                if task is asyncio.current_task():
                    continue
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            with contextlib.suppress(Exception):
                await self._phone.hangup()
            with contextlib.suppress(Exception):
                self._recorder.close()
        finally:
            self._bus.publish({"type": "call_ended", "call_id": self.call_id,
                               "outcome": outcome})
            self.done.set()
