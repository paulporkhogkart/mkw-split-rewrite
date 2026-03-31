"""EventBroadcaster — WebSocket pub-sub server for external subscribers.

Runs inside IpcServer's asyncio loop. All events emitted via IpcServer.emit()
are broadcast to every connected client. Clients are read-only (subscribe-only);
any inbound data is silently ignored.

Usage (via --ws-port flag):
    python -m mkw_tracker --ws-port 8765

External subscriber example:
    import asyncio, json, websockets

    async def main():
        async with websockets.connect("ws://localhost:8765") as ws:
            async for msg in ws:
                print(json.loads(msg))

    asyncio.run(main())
"""
import asyncio
import logging
from typing import Optional, Set

logger = logging.getLogger(__name__)


class EventBroadcaster:
    """
    WebSocket broadcast server.  Must be attached to a running asyncio loop
    via attach() before any broadcast() calls are made.
    """

    def __init__(self, port: int):
        self._port = port
        self._clients: Set = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        """Schedule the WS server startup inside an already-running asyncio loop."""
        self._loop = loop
        loop.create_task(self._serve())

    # ── Outbound ─────────────────────────────────────────────────────────────

    def broadcast(self, line: str) -> None:
        """Thread-safe: fan a JSON line out to all connected WS clients."""
        if self._loop is None or not self._clients:
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(line), self._loop)

    # ── Internals ─────────────────────────────────────────────────────────────

    async def _serve(self) -> None:
        try:
            import websockets  # type: ignore
        except ImportError:
            logger.error(
                "websockets package not installed — event broadcaster disabled. "
                "Run: pip install websockets"
            )
            return

        try:
            async with websockets.serve(self._handler, "localhost", self._port):
                logger.info("Event broadcaster listening on ws://localhost:%d", self._port)
                await asyncio.Future()  # run until the loop is stopped
        except OSError as exc:
            logger.error("Event broadcaster failed to bind on port %d: %s", self._port, exc)

    async def _handler(self, websocket) -> None:
        self._clients.add(websocket)
        addr = websocket.remote_address
        logger.info("Subscriber connected: %s (total: %d)", addr, len(self._clients))
        try:
            # Drain and discard any inbound data; keep alive until disconnect.
            async for _ in websocket:
                pass
        finally:
            self._clients.discard(websocket)
            logger.info("Subscriber disconnected: %s (total: %d)", addr, len(self._clients))

    async def _broadcast(self, line: str) -> None:
        if not self._clients:
            return
        for ws in set(self._clients):
            try:
                await ws.send(line)
            except Exception:
                # Dead connection — _handler will remove it from the set.
                pass
