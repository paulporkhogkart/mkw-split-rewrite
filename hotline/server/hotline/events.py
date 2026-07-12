from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque


class EventBus:
    """Two feeds of the same events: 'rt' fires immediately (console, bleeper
    daemon gate timing); 'delayed' fires delay_n seconds later (OBS overlay —
    delaying data beats delaying pixels, spec §7.3)."""

    def __init__(self, delay_n: float) -> None:
        self._delay_n = delay_n
        self._subs: dict[str, set[asyncio.Queue]] = {"rt": set(), "delayed": set()}
        self._pending: deque[tuple[float, dict]] = deque()  # (publish monotonic, event)
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def delay_n(self) -> float:
        return self._delay_n

    @delay_n.setter
    def delay_n(self, value: float) -> None:
        self._delay_n = value
        self._wakeup.set()

    def subscribe(self, feed: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subs[feed].add(q)
        return q

    def unsubscribe(self, feed: str, q: asyncio.Queue) -> None:
        self._subs[feed].discard(q)

    def publish(self, event: dict) -> None:
        event.setdefault("ts", time.time())
        self._fan_out("rt", event)
        self._pending.append((time.monotonic(), event))
        self._wakeup.set()

    def _fan_out(self, feed: str, event: dict) -> None:
        for q in list(self._subs[feed]):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._pump())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _pump(self) -> None:
        while True:
            if not self._pending:
                self._wakeup.clear()
                await self._wakeup.wait()
                continue
            published, event = self._pending[0]
            wait = published + self._delay_n - time.monotonic()
            if wait > 0:
                self._wakeup.clear()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wakeup.wait(), wait)
                continue  # re-derive after any wake or timeout
            self._pending.popleft()
            self._fan_out("delayed", event)
