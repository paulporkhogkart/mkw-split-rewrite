"""IpcServer — asyncio stdin reader + stdout writer for Tauri sidecar IPC."""
import asyncio
import queue
import sys
import threading
from typing import Optional

from .protocol import parse_inbound, emit_error


class IpcServer:
    """
    Runs an asyncio event loop in a daemon thread.

    - Reads newline-delimited JSON from stdin and puts parsed dicts into
      `inbound_queue` for the main loop to drain.
    - Provides `emit(line)` to write a JSON line to stdout from any thread.
    """

    def __init__(self):
        self.inbound_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stdout_lock = threading.Lock()

    # ── Start / stop ─────────────────────────────────────────────────────────

    def start(self):
        """Spawn the asyncio daemon thread."""
        self._thread = threading.Thread(target=self._run, daemon=True, name="ipc-thread")
        self._thread.start()

    def stop(self):
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)

    # ── Emit from main thread ────────────────────────────────────────────────

    def emit(self, line: str):
        """Write a JSON line to stdout (thread-safe)."""
        with self._stdout_lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._read_stdin())
        except Exception:
            pass
        finally:
            self._loop.close()

    async def _read_stdin(self):
        loop = asyncio.get_event_loop()
        while True:
            try:
                # Use a thread-pool executor so blocking readline works on
                # both TTY terminals and real pipes (Windows IOCP can't
                # attach to either with connect_read_pipe reliably).
                line = await loop.run_in_executor(None, sys.stdin.readline)
            except Exception:
                break
            if not line:
                break
            msg = parse_inbound(line)
            if msg is not None:
                self.inbound_queue.put(msg)
            else:
                self.emit(emit_error(f"Invalid JSON: {line.strip()!r}"))
