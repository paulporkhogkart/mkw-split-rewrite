from __future__ import annotations

import asyncio
import time
import uuid
from typing import Callable, Optional

IDLE = "idle"
HELD = "held"
RINGING = "ringing"
ONCALL = "oncall"


class LineBusy(Exception):
    pass


class LineLease:
    """Single-slot line lease: idle -> held -> ringing -> oncall -> idle.

    Two safety timers: the claim window kills a HELD lease that never rang
    (mic-prompt stall, closed tab), and the absolute backstop kills any lease
    regardless of state (zombie insurance, not a talk cap). Timers only ever
    *report* expiry via on_expired(lease_id); the owner decides how to tear
    down (it may need to hang up a live call first) and then calls release().
    """

    def __init__(self, publish: Callable[[dict], None],
                 claim_window_s: float, backstop_s: float) -> None:
        self._publish = publish
        self._claim_window_s = claim_window_s
        self._backstop_s = backstop_s
        self._on_expired: Optional[Callable[[str], None]] = None
        self.state = IDLE
        self.since = time.time()
        self.lease_id: Optional[str] = None
        self._window_task: Optional[asyncio.Task] = None
        self._backstop_task: Optional[asyncio.Task] = None

    def on_expired(self, cb: Callable[[str], None]) -> None:
        self._on_expired = cb

    def snapshot(self) -> dict:
        return {"type": "line_state", "state": self.state, "since": self.since}

    # -- transitions ---------------------------------------------------------
    def claim(self) -> str:
        if self.state != IDLE:
            raise LineBusy(self.state)
        self.lease_id = str(uuid.uuid4())
        self._set(HELD)
        self._window_task = asyncio.create_task(self._timer(
            self._claim_window_s, self.lease_id))
        self._backstop_task = asyncio.create_task(self._timer(
            self._backstop_s, self.lease_id))
        return self.lease_id

    def valid(self, lease_id: str) -> bool:
        return self.lease_id is not None and lease_id == self.lease_id

    def mark_ringing(self, lease_id: str) -> None:
        self._check(lease_id)
        self._cancel(self._window_task)
        self._window_task = None
        self._set(RINGING)

    def mark_oncall(self, lease_id: str) -> None:
        self._check(lease_id)
        self._set(ONCALL)

    def release(self, lease_id: Optional[str] = None) -> None:
        if lease_id is not None and not self.valid(lease_id):
            return  # stale release: already superseded, nothing to do
        self._cancel(self._window_task)
        self._cancel(self._backstop_task)
        self._window_task = self._backstop_task = None
        self.lease_id = None
        if self.state != IDLE:
            self._set(IDLE)

    # -- internals -----------------------------------------------------------
    def _check(self, lease_id: str) -> None:
        if not self.valid(lease_id):
            raise KeyError("stale lease")

    def _set(self, state: str) -> None:
        self.state = state
        self.since = time.time()
        self._publish(self.snapshot())

    @staticmethod
    def _cancel(task: Optional[asyncio.Task]) -> None:
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _timer(self, delay: float, lease_id: str) -> None:
        await asyncio.sleep(delay)
        if self.valid(lease_id) and self._on_expired:
            self._on_expired(lease_id)
