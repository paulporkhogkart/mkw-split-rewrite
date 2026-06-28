"""Read-only subscriber to the tracker broadcaster (:8766).

route() is pure; the socket loop mirrors sweep_runner.WsClient (background
asyncio thread). Connects to 127.0.0.1 (broadcaster binds IPv4; ::1 refused).
"""
import asyncio
import json
import threading


def route(msg):
    if isinstance(msg, dict) and msg.get("type") == "preview":
        return "preview", msg
    return "state", msg


class WsConsumer:
    def __init__(self, url, on_preview, on_state):
        self._url = url
        self._on_preview = on_preview
        self._on_state = on_state
        self._loop = asyncio.new_event_loop()
        self._stop = False

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="ws-consumer").start()

    def _run(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except RuntimeError:
            pass   # loop.stop() during a pending coroutine — expected on close()

    async def _main(self):
        try:
            import websockets
        except ImportError:
            self._on_state({"type": "console_error", "message": "websockets not installed"})
            return
        while not self._stop:
            try:
                async with websockets.connect(self._url) as ws:
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        kind, payload = route(msg)
                        (self._on_preview if kind == "preview" else self._on_state)(payload)
            except Exception:
                await asyncio.sleep(1.0)            # reconnect after a beat

    def close(self):
        self._stop = True
        self._loop.call_soon_threadsafe(self._loop.stop)
