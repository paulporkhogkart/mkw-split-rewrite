from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Awaitable, Callable, Optional, Protocol

from . import audio
from .events import EventBus
from .jitter import JitterBuffer
from .recording import CallRecorder

WARNING_S = 10.0
BEEP = audio.tone_frames(440.0, 200)
TIMES_UP = audio.tone_frames(300.0, 400)


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
        self._inject: list[bytes] = []      # tones mixed into both directions
        self._answered_at: Optional[float] = None
        self._warned = False
        self._pump_task: Optional[asyncio.Task] = None
        self._grace_task: Optional[asyncio.Task] = None
        self._ending = False

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        self._bus.publish({"type": "call_ringing", "call_id": self.call_id,
                           "caller": self.caller_label})
        await self._phone.ring(self.caller_label)

    def on_phone_answered(self) -> None:
        if self._answered_at is not None:
            return
        self._answered_at = time.monotonic()
        self._bus.publish({"type": "call_active", "call_id": self.call_id,
                           "caller": self.caller_label, "seconds": self.seconds})
        self._pump_task = asyncio.create_task(self._pump())

    def on_phone_hungup(self) -> None:
        asyncio.create_task(self.end(
            "completed" if self._answered_at is not None else "dropped"))

    def on_caller_frame(self, frame: bytes) -> None:
        self._jitter.push(frame)

    def on_caller_lost(self) -> None:
        if self._grace_task is None and not self._ending:
            self._grace_task = asyncio.create_task(self._grace())

    async def _grace(self) -> None:
        await asyncio.sleep(self._grace_s)
        await self.end("dropped")

    def on_phone_frame(self, frame: bytes) -> None:
        self._recorder.add_phone(frame)
        asyncio.create_task(self._send_to_caller(frame))

    # -- 20 ms pump: caller->phone, timer, tone injection --------------------
    async def _pump(self) -> None:
        next_t = time.monotonic()
        try:
            while not self._ending:
                frame = self._jitter.pull()
                if self._inject:
                    tone = self._inject.pop(0)
                    frame = audio.mix_frames(frame, tone)
                    await self._send_to_caller(tone)   # both directions
                self._recorder.add_caller(frame)
                await self._phone.send_frame(frame)

                elapsed = time.monotonic() - self._answered_at
                self.seconds_used = elapsed
                remaining = self.seconds - elapsed
                if not self._warned and remaining <= min(WARNING_S, self.seconds):
                    self._warned = True
                    self._bus.publish({"type": "call_warning",
                                       "call_id": self.call_id})
                    self._inject.extend(BEEP + BEEP)
                if remaining <= 0:
                    for tone in TIMES_UP:
                        await self._phone.send_frame(tone)
                        await self._send_to_caller(tone)
                    await self._phone.hangup()
                    return

                next_t += audio.FRAME_MS / 1000
                await asyncio.sleep(max(0.0, next_t - time.monotonic()))
        except asyncio.CancelledError:
            pass

    async def end(self, outcome: str) -> None:
        if self._ending:
            return
        self._ending = True
        self.outcome = outcome
        for task in (self._pump_task, self._grace_task):
            if task and task is not asyncio.current_task():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        with contextlib.suppress(Exception):
            await self._phone.hangup()
        self._recorder.close()
        self._bus.publish({"type": "call_ended", "call_id": self.call_id,
                           "outcome": outcome})
        self.done.set()
