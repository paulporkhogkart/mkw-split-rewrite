# Pork Phone Plan 1 — Hotline Server Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Pi-side hotline service — browser-audio WebSocket in, AudioSocket to
Asterisk out, call lifecycle, events, recordings — fully testable with WAV files and no
purchased hardware, ending with a first internet ring against MicroSIP standing in for the ATA.

**Architecture:** One Python asyncio service (`hotline/server/`) per the approved spec
(`docs/superpowers/specs/2026-07-12-pork-phone-hotline-design.md`). The browser sends/receives
raw 16-bit 8 kHz mono PCM in 20 ms (320-byte) frames over WSS (resampling happens in the
browser worklet — no codecs anywhere). The app is an AudioSocket **TCP server** that Asterisk
connects out to; call control uses ARI. Everything binds localhost except nothing — the
Cloudflare tunnel's origin is localhost; Asterisk owns the only LAN-facing socket (SIP, ACL'd).

**Tech Stack:** Python ≥ 3.11, `aiohttp` (HTTP/WS server + ARI client), `numpy` (mixing),
stdlib `sqlite3`, `pytest` + `pytest-asyncio` (auto mode). Asterisk 20 (Pi OS Bookworm apt)
+ MicroSIP (free softphone) as the Phase-1 stand-in for the HT802V2.

## Global Constraints

- **Spec is law:** `docs/superpowers/specs/2026-07-12-pork-phone-hotline-design.md` (APPROVED 2026-07-12). Deviations require Paul.
- **Delay N = 4 s** default (`HOTLINE_DELAY_N=4`), runtime-tunable; delayed event feed uses it.
- **Zero `pi/` imports.** The hotline never touches the site's code or DB.
- **App listeners bind `127.0.0.1` only** (HTTP `9100`, AudioSocket `9101`). The tunnel and Asterisk connect via loopback. Only Asterisk binds a LAN interface (SIP, with ATA-IP ACL — its own config, Task 14).
- **Audio frame contract everywhere:** 16-bit signed LE, 8000 Hz, mono, 20 ms ⇒ **320 bytes/frame, 160 samples**.
- **Fail closed:** lines closed on boot; unknown AudioSocket UUIDs get TERMINATE; missing/bad token ⇒ 401; recording-dir free space < 1 GiB ⇒ lines refuse to open.
- **Secrets via environment** (systemd `EnvironmentFile`), never in the repo. Dev default token only when `HOTLINE_ENV=dev`.
- Python style: type hints, `from __future__ import annotations`, no global state outside `__main__` composition.
- Dev machine is Windows; every test task must pass with `python -m pytest` from `hotline/server/`. Deploy targets the Pi (Linux). No `sounddevice`/audio-device use in this plan.
- Commit after every task (conventional commits, `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`).

**Out of scope for Plan 1** (later plans / runbooks): Twitch OAuth/EventSub/queue/credits
(Plan 2), `/phone` page in `web/` (Plan 2), console/overlay UIs (Plan 2), bleeper daemon
(Plan 3), UDM zone config + ATA bench (spec §14 runbook, Paul-driven when parts arrive).

---

### Task 1: Package scaffold, config, health endpoint

**Files:**
- Create: `hotline/server/requirements.txt`, `hotline/server/requirements-dev.txt`, `hotline/server/pytest.ini`, `hotline/server/README.md`
- Create: `hotline/server/hotline/__init__.py`, `hotline/server/hotline/config.py`, `hotline/server/hotline/http.py`
- Test: `hotline/server/tests/test_config.py`, `hotline/server/tests/test_http.py`

**Interfaces:**
- Produces: `Config` dataclass — fields `env: str`, `http_port: int (9100)`, `audiosocket_port: int (9101)`, `admin_token: str`, `data_dir: Path`, `delay_n: float (4.0)`, `ari_url: str`, `ari_user: str`, `ari_password: str`, `echo_mode: bool`; classmethod `Config.from_env(environ: Mapping[str, str]) -> Config`.
- Produces: `make_app(cfg: Config, controller=None) -> aiohttp.web.Application` in `http.py` with `GET /healthz` → `200 {"ok": true}` (controller wired in Task 11).

- [ ] **Step 1: Scaffold files**

`hotline/server/requirements.txt`:
```
aiohttp>=3.9
numpy>=1.26
```

`hotline/server/requirements-dev.txt`:
```
-r requirements.txt
pytest>=8
pytest-asyncio>=0.23
```

`hotline/server/pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

`hotline/server/hotline/__init__.py`: empty file.

`hotline/server/README.md`:
```markdown
# hotline/server — the Pork Phone Pi service

Spec: docs/superpowers/specs/2026-07-12-pork-phone-hotline-design.md

Run tests:   cd hotline/server && python -m pytest
Run app:     cd hotline/server && python -m hotline
Audio contract: 16-bit LE mono 8 kHz PCM, 20 ms frames (320 bytes).
WS /ws/audio: binary messages = one 320-byte frame; text messages = JSON control.
WS /ws/events?feed=rt|delayed&token=... : JSON events.
Env: HOTLINE_ENV, HOTLINE_HTTP_PORT, HOTLINE_AUDIOSOCKET_PORT, HOTLINE_ADMIN_TOKEN,
     HOTLINE_DATA_DIR, HOTLINE_DELAY_N, HOTLINE_ARI_URL, HOTLINE_ARI_USER,
     HOTLINE_ARI_PASSWORD, HOTLINE_ECHO (dev echo mode).
```

- [ ] **Step 2: Write failing tests**

`hotline/server/tests/test_config.py`:
```python
from __future__ import annotations

import pytest

from hotline.config import Config


def test_from_env_defaults_dev():
    cfg = Config.from_env({"HOTLINE_ENV": "dev", "HOTLINE_DATA_DIR": "./tmpdata"})
    assert cfg.http_port == 9100
    assert cfg.audiosocket_port == 9101
    assert cfg.delay_n == 4.0
    assert cfg.admin_token == "dev-token"
    assert cfg.echo_mode is False


def test_prod_requires_token():
    with pytest.raises(ValueError, match="HOTLINE_ADMIN_TOKEN"):
        Config.from_env({"HOTLINE_ENV": "prod", "HOTLINE_DATA_DIR": "./d"})


def test_overrides_parse():
    cfg = Config.from_env({
        "HOTLINE_ENV": "prod", "HOTLINE_DATA_DIR": "/opt/hotline-data",
        "HOTLINE_ADMIN_TOKEN": "s3cret", "HOTLINE_HTTP_PORT": "9200",
        "HOTLINE_DELAY_N": "2.5", "HOTLINE_ECHO": "1",
    })
    assert cfg.http_port == 9200 and cfg.delay_n == 2.5
    assert cfg.admin_token == "s3cret" and cfg.echo_mode is True
```

`hotline/server/tests/test_http.py`:
```python
from __future__ import annotations

from aiohttp.test_utils import TestClient, TestServer

from hotline.config import Config
from hotline.http import make_app


async def test_healthz(tmp_path):
    cfg = Config.from_env({"HOTLINE_ENV": "dev", "HOTLINE_DATA_DIR": str(tmp_path)})
    async with TestClient(TestServer(make_app(cfg))) as client:
        resp = await client.get("/healthz")
        assert resp.status == 200
        assert (await resp.json()) == {"ok": True}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd hotline/server; python -m pip install -r requirements-dev.txt; python -m pytest -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hotline.config'`

- [ ] **Step 4: Implement**

`hotline/server/hotline/config.py`:
```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Config:
    env: str
    http_port: int
    audiosocket_port: int
    admin_token: str
    data_dir: Path
    delay_n: float
    ari_url: str
    ari_user: str
    ari_password: str
    echo_mode: bool

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> "Config":
        env = environ.get("HOTLINE_ENV", "dev")
        token = environ.get("HOTLINE_ADMIN_TOKEN", "")
        if not token:
            if env == "dev":
                token = "dev-token"
            else:
                raise ValueError("HOTLINE_ADMIN_TOKEN is required outside dev")
        data_dir = environ.get("HOTLINE_DATA_DIR", "")
        if not data_dir:
            raise ValueError("HOTLINE_DATA_DIR is required")
        return cls(
            env=env,
            http_port=int(environ.get("HOTLINE_HTTP_PORT", "9100")),
            audiosocket_port=int(environ.get("HOTLINE_AUDIOSOCKET_PORT", "9101")),
            admin_token=token,
            data_dir=Path(data_dir),
            delay_n=float(environ.get("HOTLINE_DELAY_N", "4")),
            ari_url=environ.get("HOTLINE_ARI_URL", "http://127.0.0.1:8088"),
            ari_user=environ.get("HOTLINE_ARI_USER", "hotline"),
            ari_password=environ.get("HOTLINE_ARI_PASSWORD", ""),
            echo_mode=environ.get("HOTLINE_ECHO", "") in ("1", "true", "yes"),
        )
```

`hotline/server/hotline/http.py`:
```python
from __future__ import annotations

from aiohttp import web

from .config import Config


async def _healthz(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def make_app(cfg: Config, controller=None) -> web.Application:
    app = web.Application()
    app["cfg"] = cfg
    app["controller"] = controller
    app.router.add_get("/healthz", _healthz)
    return app
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd hotline/server; python -m pytest -q`
Expected: `4 passed`

- [ ] **Step 6: Commit**

```bash
git add hotline/server
git commit -m "feat(hotline): server scaffold, config, healthz"
```

---

### Task 2: Audio primitives (`audio.py`)

**Files:**
- Create: `hotline/server/hotline/audio.py`
- Test: `hotline/server/tests/test_audio.py`

**Interfaces:**
- Produces (module constants): `SAMPLE_RATE = 8000`, `FRAME_MS = 20`, `SAMPLES_PER_FRAME = 160`, `FRAME_BYTES = 320`, `SILENCE_FRAME: bytes` (320 zero bytes).
- Produces: `tone_frames(freq_hz: float, ms: int, amplitude: float = 0.3) -> list[bytes]` — int16 sine, whole frames.
- Produces: `wav_read_frames(path: Path) -> list[bytes]` (asserts 8 kHz mono 16-bit) · `wav_write_frames(path: Path, frames: Iterable[bytes]) -> None` · `mix_frames(a: bytes, b: bytes) -> bytes` (int32 add, clip to int16).

- [ ] **Step 1: Write failing tests**

`hotline/server/tests/test_audio.py`:
```python
from __future__ import annotations

import numpy as np

from hotline import audio


def test_constants():
    assert audio.FRAME_BYTES == 320 and audio.SAMPLES_PER_FRAME == 160
    assert audio.SILENCE_FRAME == b"\x00" * 320


def test_tone_frames_shape_and_energy():
    frames = audio.tone_frames(440.0, 100)  # 100 ms => 5 frames
    assert len(frames) == 5 and all(len(f) == 320 for f in frames)
    pcm = np.frombuffer(b"".join(frames), dtype=np.int16)
    assert np.abs(pcm).max() > 5000  # audible


def test_wav_roundtrip(tmp_path):
    frames = audio.tone_frames(300.0, 60)
    p = tmp_path / "t.wav"
    audio.wav_write_frames(p, frames)
    assert audio.wav_read_frames(p) == frames


def test_mix_clips_not_wraps():
    loud = (np.full(160, 30000, dtype=np.int16)).tobytes()
    mixed = audio.mix_frames(loud, loud)
    pcm = np.frombuffer(mixed, dtype=np.int16)
    assert pcm.max() == 32767  # saturated, not wrapped negative
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd hotline/server; python -m pytest tests/test_audio.py -q`
Expected: FAIL — `No module named 'hotline.audio'`

- [ ] **Step 3: Implement**

`hotline/server/hotline/audio.py`:
```python
from __future__ import annotations

import math
import wave
from pathlib import Path
from typing import Iterable

import numpy as np

SAMPLE_RATE = 8000
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000  # 160
FRAME_BYTES = SAMPLES_PER_FRAME * 2  # 320
SILENCE_FRAME = b"\x00" * FRAME_BYTES


def tone_frames(freq_hz: float, ms: int, amplitude: float = 0.3) -> list[bytes]:
    n_frames = max(1, ms // FRAME_MS)
    total = n_frames * SAMPLES_PER_FRAME
    t = np.arange(total) / SAMPLE_RATE
    pcm = (np.sin(2 * math.pi * freq_hz * t) * amplitude * 32767).astype(np.int16)
    raw = pcm.tobytes()
    return [raw[i : i + FRAME_BYTES] for i in range(0, len(raw), FRAME_BYTES)]


def wav_write_frames(path: Path, frames: Iterable[bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        for f in frames:
            w.writeframes(f)


def wav_read_frames(path: Path) -> list[bytes]:
    with wave.open(str(path), "rb") as r:
        assert r.getnchannels() == 1 and r.getsampwidth() == 2
        assert r.getframerate() == SAMPLE_RATE
        raw = r.readframes(r.getnframes())
    return [raw[i : i + FRAME_BYTES] for i in range(0, len(raw) - len(raw) % FRAME_BYTES, FRAME_BYTES)]


def mix_frames(a: bytes, b: bytes) -> bytes:
    pa = np.frombuffer(a, dtype=np.int16).astype(np.int32)
    pb = np.frombuffer(b, dtype=np.int16).astype(np.int32)
    n = max(len(pa), len(pb))
    pa = np.pad(pa, (0, n - len(pa)))
    pb = np.pad(pb, (0, n - len(pb)))
    return np.clip(pa + pb, -32768, 32767).astype(np.int16).tobytes()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd hotline/server; python -m pytest tests/test_audio.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add hotline/server/hotline/audio.py hotline/server/tests/test_audio.py
git commit -m "feat(hotline): audio primitives — frames, tones, WAV, saturating mix"
```

---

### Task 3: Jitter buffer (`jitter.py`)

**Files:**
- Create: `hotline/server/hotline/jitter.py`
- Test: `hotline/server/tests/test_jitter.py`

**Interfaces:**
- Produces: `JitterBuffer(target_ms: int = 100, max_ms: int = 400)` with `push(frame: bytes) -> None` and `pull() -> bytes` (caller ticks it every 20 ms; returns `SILENCE_FRAME` while pre-buffering or on underrun; drops oldest above `max_ms`). Pure/deterministic — no clocks, no asyncio.

- [ ] **Step 1: Write failing tests**

`hotline/server/tests/test_jitter.py`:
```python
from __future__ import annotations

from hotline.audio import SILENCE_FRAME
from hotline.jitter import JitterBuffer


def frame(i: int) -> bytes:
    return bytes([i % 256]) * 320


def test_prebuffers_to_target_then_plays():
    jb = JitterBuffer(target_ms=60, max_ms=400)  # target = 3 frames
    assert jb.pull() == SILENCE_FRAME
    jb.push(frame(1)); jb.push(frame(2))
    assert jb.pull() == SILENCE_FRAME          # only 2 < 3 buffered
    jb.push(frame(3))
    assert jb.pull() == frame(1)
    assert jb.pull() == frame(2)


def test_underrun_emits_silence_and_rebuffers():
    jb = JitterBuffer(target_ms=40, max_ms=400)  # target = 2
    jb.push(frame(1)); jb.push(frame(2))
    assert jb.pull() == frame(1) and jb.pull() == frame(2)
    assert jb.pull() == SILENCE_FRAME          # underrun
    jb.push(frame(3))
    assert jb.pull() == SILENCE_FRAME          # rebuffering: 1 < 2
    jb.push(frame(4))
    assert jb.pull() == frame(3)


def test_overrun_drops_oldest():
    jb = JitterBuffer(target_ms=20, max_ms=60)  # max = 3 frames
    for i in range(1, 6):
        jb.push(frame(i))
    assert jb.pull() == frame(3)               # 1 and 2 were dropped
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd hotline/server; python -m pytest tests/test_jitter.py -q`
Expected: FAIL — `No module named 'hotline.jitter'`

- [ ] **Step 3: Implement**

`hotline/server/hotline/jitter.py`:
```python
from __future__ import annotations

from collections import deque

from .audio import FRAME_MS, SILENCE_FRAME


class JitterBuffer:
    """Absorbs network arrival jitter on the caller->phone path.

    push() on packet arrival; pull() on the 20 ms pump tick. Emits silence
    while pre-buffering (start and after any underrun) so the downstream
    clock never starves; drops oldest frames beyond max_ms so latency can
    never grow unbounded.
    """

    def __init__(self, target_ms: int = 100, max_ms: int = 400) -> None:
        self._frames: deque[bytes] = deque()
        self._target = max(1, target_ms // FRAME_MS)
        self._max = max(self._target, max_ms // FRAME_MS)
        self._playing = False

    def push(self, frame: bytes) -> None:
        self._frames.append(frame)
        while len(self._frames) > self._max:
            self._frames.popleft()

    def pull(self) -> bytes:
        if not self._playing:
            if len(self._frames) < self._target:
                return SILENCE_FRAME
            self._playing = True
        if self._frames:
            return self._frames.popleft()
        self._playing = False
        return SILENCE_FRAME
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd hotline/server; python -m pytest tests/test_jitter.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add hotline/server/hotline/jitter.py hotline/server/tests/test_jitter.py
git commit -m "feat(hotline): deterministic jitter buffer"
```

---

### Task 4: AudioSocket server (`audiosocket.py`)

Asterisk's AudioSocket protocol (res_audiosocket): each message is
`kind(1 byte) + length(2 bytes, big-endian) + payload`. Kinds: `0x00` TERMINATE,
`0x01` UUID (16-byte binary UUID, first message of a session), `0x03` DTMF,
`0x10` AUDIO (320 bytes slin 8 kHz/20 ms), `0xff` ERROR. Asterisk **connects out to us**;
we are the TCP server. (Re-verify kinds against the installed Asterisk's
`res_audiosocket` docs during Task 15 — protocol is long-stable.)

**Files:**
- Create: `hotline/server/hotline/audiosocket.py`
- Test: `hotline/server/tests/test_audiosocket.py`

**Interfaces:**
- Produces: `KIND_TERMINATE=0x00, KIND_UUID=0x01, KIND_DTMF=0x03, KIND_AUDIO=0x10, KIND_ERROR=0xFF`; `encode_frame(kind: int, payload: bytes) -> bytes`; `async read_frame(reader) -> tuple[int, bytes]` (raises `IncompleteReadError` on EOF).
- Produces: `class AudioSocketSession` — attrs `uuid: uuid.UUID`; methods `async send_audio(frame: bytes)`, `async terminate()`, `def on_audio(cb: Callable[[bytes], None])`, `def on_closed(cb: Callable[[], None])`.
- Produces: `class AudioSocketServer(port: int, on_session: Callable[[AudioSocketSession], Awaitable[bool]])` — `await start()` / `await stop()`; binds `127.0.0.1`; reads the UUID frame, calls `on_session`; **if `on_session` returns False (unknown UUID), sends TERMINATE and closes** (fail closed).

- [ ] **Step 1: Write failing tests**

`hotline/server/tests/test_audiosocket.py`:
```python
from __future__ import annotations

import asyncio
import uuid

from hotline import audiosocket as aus
from hotline.audio import SILENCE_FRAME


def test_encode_frame():
    b = aus.encode_frame(aus.KIND_AUDIO, b"\x01\x02")
    assert b == bytes([0x10, 0x00, 0x02, 0x01, 0x02])


async def _connect(port: int, u: uuid.UUID):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(aus.encode_frame(aus.KIND_UUID, u.bytes))
    await writer.drain()
    return reader, writer


async def test_session_roundtrip(unused_tcp_port):
    got_audio: list[bytes] = []
    closed = asyncio.Event()
    sessions: list[aus.AudioSocketSession] = []

    async def on_session(sess: aus.AudioSocketSession) -> bool:
        sessions.append(sess)
        sess.on_audio(got_audio.append)
        sess.on_closed(closed.set)
        return True

    server = aus.AudioSocketServer(unused_tcp_port, on_session)
    await server.start()
    u = uuid.uuid4()
    reader, writer = await _connect(unused_tcp_port, u)
    await asyncio.sleep(0.05)
    assert sessions and sessions[0].uuid == u

    # client -> server audio
    writer.write(aus.encode_frame(aus.KIND_AUDIO, SILENCE_FRAME))
    await writer.drain()
    await asyncio.sleep(0.05)
    assert got_audio == [SILENCE_FRAME]

    # server -> client audio
    await sessions[0].send_audio(SILENCE_FRAME)
    kind, payload = await aus.read_frame(reader)
    assert kind == aus.KIND_AUDIO and payload == SILENCE_FRAME

    writer.close()
    await asyncio.wait_for(closed.wait(), 2)
    await server.stop()


async def test_unknown_uuid_terminated(unused_tcp_port):
    async def on_session(_sess: aus.AudioSocketSession) -> bool:
        return False  # unknown call

    server = aus.AudioSocketServer(unused_tcp_port, on_session)
    await server.start()
    reader, writer = await _connect(unused_tcp_port, uuid.uuid4())
    kind, _ = await aus.read_frame(reader)
    assert kind == aus.KIND_TERMINATE
    writer.close()
    await server.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd hotline/server; python -m pytest tests/test_audiosocket.py -q`
Expected: FAIL — `No module named 'hotline.audiosocket'`

- [ ] **Step 3: Implement**

`hotline/server/hotline/audiosocket.py`:
```python
from __future__ import annotations

import asyncio
import contextlib
import uuid as uuidlib
from typing import Awaitable, Callable, Optional

KIND_TERMINATE = 0x00
KIND_UUID = 0x01
KIND_DTMF = 0x03
KIND_AUDIO = 0x10
KIND_ERROR = 0xFF


def encode_frame(kind: int, payload: bytes) -> bytes:
    return bytes([kind]) + len(payload).to_bytes(2, "big") + payload


async def read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    header = await reader.readexactly(3)
    length = int.from_bytes(header[1:3], "big")
    payload = await reader.readexactly(length) if length else b""
    return header[0], payload


class AudioSocketSession:
    def __init__(self, uuid: uuidlib.UUID, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter) -> None:
        self.uuid = uuid
        self._reader = reader
        self._writer = writer
        self._on_audio: Optional[Callable[[bytes], None]] = None
        self._on_closed: Optional[Callable[[], None]] = None
        self._closed = False

    def on_audio(self, cb: Callable[[bytes], None]) -> None:
        self._on_audio = cb

    def on_closed(self, cb: Callable[[], None]) -> None:
        self._on_closed = cb

    async def send_audio(self, frame: bytes) -> None:
        if self._closed:
            return
        self._writer.write(encode_frame(KIND_AUDIO, frame))
        await self._writer.drain()

    async def terminate(self) -> None:
        if self._closed:
            return
        with contextlib.suppress(ConnectionError):
            self._writer.write(encode_frame(KIND_TERMINATE, b""))
            await self._writer.drain()
        self._close()

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(ConnectionError):
            self._writer.close()
        if self._on_closed:
            self._on_closed()

    async def run(self) -> None:
        try:
            while True:
                kind, payload = await read_frame(self._reader)
                if kind == KIND_AUDIO and self._on_audio:
                    self._on_audio(payload)
                elif kind in (KIND_TERMINATE, KIND_ERROR):
                    break
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._close()


class AudioSocketServer:
    def __init__(self, port: int,
                 on_session: Callable[[AudioSocketSession], Awaitable[bool]]) -> None:
        self._port = port
        self._on_session = on_session
        self._server: Optional[asyncio.base_events.Server] = None
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", self._port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for t in list(self._tasks):
            t.cancel()

    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        try:
            kind, payload = await asyncio.wait_for(read_frame(reader), 5)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return
        if kind != KIND_UUID or len(payload) != 16:
            writer.write(encode_frame(KIND_TERMINATE, b""))
            writer.close()
            return
        sess = AudioSocketSession(uuidlib.UUID(bytes=payload), reader, writer)
        accepted = await self._on_session(sess)
        if not accepted:
            await sess.terminate()
            return
        task = asyncio.create_task(sess.run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd hotline/server; python -m pytest tests/test_audiosocket.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add hotline/server/hotline/audiosocket.py hotline/server/tests/test_audiosocket.py
git commit -m "feat(hotline): AudioSocket TCP server (Asterisk dials us)"
```

---

### Task 5: Database (`schema.sql`, `db.py`)

Schema per spec §12 (all tables now so Plan 2 never migrates; Plan 1 only writes
`calls` + `settings`).

**Files:**
- Create: `hotline/server/hotline/schema.sql`, `hotline/server/hotline/db.py`
- Test: `hotline/server/tests/test_db.py`

**Interfaces:**
- Produces: `class Db(path: Path)` — sync sqlite3 wrapper (call via `asyncio.to_thread` from async code); methods `init()` (idempotent), `create_call(call_id: str, caller_label: str, seconds_bought: int) -> None`, `finish_call(call_id: str, outcome: str, seconds_used: int, recording_dir: str) -> None`, `add_strike(call_id: str, at_ms: int, span_ms: int, action: str) -> None`, `get_setting(key: str, default: str) -> str`, `set_setting(key: str, value: str) -> None`, `close()`.

- [ ] **Step 1: Write failing tests**

`hotline/server/tests/test_db.py`:
```python
from __future__ import annotations

from hotline.db import Db


def test_init_idempotent(tmp_path):
    db = Db(tmp_path / "h.db")
    db.init()
    db.init()  # second run must not raise
    db.close()


def test_call_roundtrip(tmp_path):
    db = Db(tmp_path / "h.db")
    db.init()
    db.create_call("c1", "twitch:pork_fan", 60)
    db.finish_call("c1", "completed", 58, "recordings/c1")
    row = db._conn.execute(
        "SELECT caller_label, outcome, seconds_used FROM calls WHERE call_id='c1'"
    ).fetchone()
    assert tuple(row) == ("twitch:pork_fan", "completed", 58)
    db.add_strike("c1", 12000, 4000, "dump")
    n = db._conn.execute("SELECT COUNT(*) FROM strikes").fetchone()[0]
    assert n == 1
    db.close()


def test_settings(tmp_path):
    db = Db(tmp_path / "h.db")
    db.init()
    assert db.get_setting("delay_n", "4") == "4"
    db.set_setting("delay_n", "2")
    assert db.get_setting("delay_n", "4") == "2"
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd hotline/server; python -m pytest tests/test_db.py -q`
Expected: FAIL — `No module named 'hotline.db'`

- [ ] **Step 3: Implement**

`hotline/server/hotline/schema.sql`:
```sql
-- Pork Phone hotline schema (spec §12). Own DB; zero overlap with pi/ tables.
CREATE TABLE IF NOT EXISTS identities (
  twitch_user_id TEXT PRIMARY KEY,
  display_name   TEXT NOT NULL,
  avatar_url     TEXT,
  created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS credits (
  id             INTEGER PRIMARY KEY,
  twitch_user_id TEXT NOT NULL,
  seconds        INTEGER NOT NULL,
  source         TEXT NOT NULL,             -- channel_points | stripe | ...
  status         TEXT NOT NULL DEFAULT 'unspent',  -- unspent|reserved|spent|refunded
  redemption_id  TEXT,
  created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS bans (
  twitch_user_id TEXT PRIMARY KEY,
  reason         TEXT NOT NULL,
  strike_call_id TEXT,
  created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS calls (
  call_id        TEXT PRIMARY KEY,
  caller_label   TEXT NOT NULL,             -- Plan 1: freeform; Plan 2: twitch id
  seconds_bought INTEGER NOT NULL,
  seconds_used   INTEGER,
  outcome        TEXT,                      -- completed|dropped|banned|test
  consent_at     TEXT,
  recording_dir  TEXT,
  started_at     TEXT NOT NULL DEFAULT (datetime('now')),
  ended_at       TEXT
);
CREATE TABLE IF NOT EXISTS strikes (
  id       INTEGER PRIMARY KEY,
  call_id  TEXT NOT NULL REFERENCES calls(call_id),
  at_ms    INTEGER NOT NULL,                -- offset into the call
  span_ms  INTEGER NOT NULL,
  action   TEXT NOT NULL,                   -- dump|ban
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
```

`hotline/server/hotline/db.py`:
```python
from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path


class Db:
    """Synchronous sqlite wrapper. One-call-at-a-time scale: call from async
    code via asyncio.to_thread for writes; never hold the connection across
    awaits."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")

    def init(self) -> None:
        schema = resources.files("hotline").joinpath("schema.sql").read_text()
        self._conn.executescript(schema)
        self._conn.commit()

    def create_call(self, call_id: str, caller_label: str, seconds_bought: int) -> None:
        self._conn.execute(
            "INSERT INTO calls (call_id, caller_label, seconds_bought) VALUES (?,?,?)",
            (call_id, caller_label, seconds_bought))
        self._conn.commit()

    def finish_call(self, call_id: str, outcome: str, seconds_used: int,
                    recording_dir: str) -> None:
        self._conn.execute(
            "UPDATE calls SET outcome=?, seconds_used=?, recording_dir=?, "
            "ended_at=datetime('now') WHERE call_id=?",
            (outcome, seconds_used, recording_dir, call_id))
        self._conn.commit()

    def add_strike(self, call_id: str, at_ms: int, span_ms: int, action: str) -> None:
        self._conn.execute(
            "INSERT INTO strikes (call_id, at_ms, span_ms, action) VALUES (?,?,?,?)",
            (call_id, at_ms, span_ms, action))
        self._conn.commit()

    def get_setting(self, key: str, default: str) -> str:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO settings (key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd hotline/server; python -m pytest tests/test_db.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add hotline/server/hotline/schema.sql hotline/server/hotline/db.py hotline/server/tests/test_db.py
git commit -m "feat(hotline): sqlite schema (spec §12) and Db wrapper"
```

---

### Task 6: Event bus with real-time + N-delayed feeds (`events.py`)

**Files:**
- Create: `hotline/server/hotline/events.py`
- Test: `hotline/server/tests/test_events.py`

**Interfaces:**
- Produces: `class EventBus(delay_n: float)` — `publish(event: dict) -> None` (stamps `"ts"` epoch float if absent); `subscribe(feed: str) -> asyncio.Queue[dict]` with `feed in ("rt","delayed")`; `unsubscribe(feed: str, q) -> None`; `async start()` / `async stop()` (delayed-feed pump task); property `delay_n: float` (mutable at runtime — the knob).
- Event vocabulary used by later tasks: `{"type": "lines_state", "open": bool}` · `{"type": "call_ringing", "call_id", "caller"}` · `{"type": "call_active", "call_id", "caller", "seconds"}` · `{"type": "call_warning", "call_id"}` · `{"type": "call_ended", "call_id", "outcome"}`.

- [ ] **Step 1: Write failing tests**

`hotline/server/tests/test_events.py`:
```python
from __future__ import annotations

import asyncio
import time

from hotline.events import EventBus


async def test_rt_is_immediate_delayed_waits():
    bus = EventBus(delay_n=0.15)
    await bus.start()
    rt = bus.subscribe("rt")
    delayed = bus.subscribe("delayed")
    t0 = time.monotonic()
    bus.publish({"type": "call_ringing", "call_id": "c1", "caller": "x"})

    ev_rt = await asyncio.wait_for(rt.get(), 0.1)
    assert ev_rt["type"] == "call_ringing" and (time.monotonic() - t0) < 0.1

    ev_d = await asyncio.wait_for(delayed.get(), 1.0)
    assert ev_d["type"] == "call_ringing"
    assert (time.monotonic() - t0) >= 0.14  # held for ~delay_n
    await bus.stop()


async def test_unsubscribe_stops_delivery():
    bus = EventBus(delay_n=0.01)
    await bus.start()
    q = bus.subscribe("rt")
    bus.unsubscribe("rt", q)
    bus.publish({"type": "lines_state", "open": True})
    await asyncio.sleep(0.05)
    assert q.empty()
    await bus.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd hotline/server; python -m pytest tests/test_events.py -q`
Expected: FAIL — `No module named 'hotline.events'`

- [ ] **Step 3: Implement**

`hotline/server/hotline/events.py`:
```python
from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from typing import Optional


class EventBus:
    """Two feeds of the same events: 'rt' fires immediately (console, bleeper
    daemon gate timing); 'delayed' fires delay_n seconds later (OBS overlay —
    delaying data beats delaying pixels, spec §7.3)."""

    def __init__(self, delay_n: float) -> None:
        self.delay_n = delay_n
        self._subs: dict[str, set[asyncio.Queue]] = {"rt": set(), "delayed": set()}
        self._pending: deque[tuple[float, dict]] = deque()
        self._wakeup = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    def subscribe(self, feed: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subs[feed].add(q)
        return q

    def unsubscribe(self, feed: str, q: asyncio.Queue) -> None:
        self._subs[feed].discard(q)

    def publish(self, event: dict) -> None:
        event.setdefault("ts", time.time())
        self._fan_out("rt", event)
        self._pending.append((time.monotonic() + self.delay_n, event))
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
            due, event = self._pending[0]
            wait = due - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._pending.popleft()
            self._fan_out("delayed", event)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd hotline/server; python -m pytest tests/test_events.py -q`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add hotline/server/hotline/events.py hotline/server/tests/test_events.py
git commit -m "feat(hotline): event bus with rt + N-delayed feeds"
```

---

### Task 7: Per-call recorder + retention sweep (`recording.py`)

**Files:**
- Create: `hotline/server/hotline/recording.py`
- Test: `hotline/server/tests/test_recording.py`

**Interfaces:**
- Consumes: `audio.wav_write_frames`, `audio.wav_read_frames`, `audio.mix_frames`, `audio.SILENCE_FRAME`.
- Produces: `class CallRecorder(dir: Path)` — `add_caller(frame: bytes)`, `add_phone(frame: bytes)`, `close() -> Path` (writes `caller.wav`, `phone.wav`, `mix.wav` — mix pads the shorter leg with silence; returns dir).
- Produces: `sweep_retention(recordings_root: Path, max_age_days: int = 90, now: float | None = None) -> int` — deletes call dirs older than the cutoff (by mtime), returns count.
- Produces: `free_space_gib(path: Path) -> float` (via `shutil.disk_usage`; used by the lines-open guard in Task 11).

- [ ] **Step 1: Write failing tests**

`hotline/server/tests/test_recording.py`:
```python
from __future__ import annotations

import os
import time

from hotline import audio
from hotline.recording import CallRecorder, free_space_gib, sweep_retention


def test_records_both_legs_and_mix(tmp_path):
    rec = CallRecorder(tmp_path / "call1")
    tone = audio.tone_frames(440.0, 40)      # 2 frames
    for f in tone:
        rec.add_caller(f)
    rec.add_phone(audio.SILENCE_FRAME)       # phone leg shorter (1 frame)
    out = rec.close()
    caller = audio.wav_read_frames(out / "caller.wav")
    phone = audio.wav_read_frames(out / "phone.wav")
    mix = audio.wav_read_frames(out / "mix.wav")
    assert caller == tone and phone == [audio.SILENCE_FRAME]
    assert len(mix) == 2                     # padded to longer leg
    assert mix[0] == audio.mix_frames(tone[0], audio.SILENCE_FRAME)


def test_sweep_retention(tmp_path):
    old = tmp_path / "old_call"; old.mkdir()
    (old / "mix.wav").write_bytes(b"x")
    stale = time.time() - 91 * 86400
    os.utime(old, (stale, stale))
    fresh = tmp_path / "fresh_call"; fresh.mkdir()
    deleted = sweep_retention(tmp_path, max_age_days=90)
    assert deleted == 1
    assert not old.exists() and fresh.exists()


def test_free_space_positive(tmp_path):
    assert free_space_gib(tmp_path) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd hotline/server; python -m pytest tests/test_recording.py -q`
Expected: FAIL — `No module named 'hotline.recording'`

- [ ] **Step 3: Implement**

`hotline/server/hotline/recording.py`:
```python
from __future__ import annotations

import shutil
import time
from pathlib import Path

from . import audio


class CallRecorder:
    """Buffers both legs in memory (one call at a time; 10 min ≈ 9.6 MB/leg)
    and writes caller.wav / phone.wav / mix.wav at close. Recordings are RAW
    (pre-dump) per spec §12 — the dump log lives in the strikes table."""

    def __init__(self, dir: Path) -> None:
        self._dir = dir
        self._caller: list[bytes] = []
        self._phone: list[bytes] = []

    def add_caller(self, frame: bytes) -> None:
        self._caller.append(frame)

    def add_phone(self, frame: bytes) -> None:
        self._phone.append(frame)

    def close(self) -> Path:
        audio.wav_write_frames(self._dir / "caller.wav", self._caller)
        audio.wav_write_frames(self._dir / "phone.wav", self._phone)
        n = max(len(self._caller), len(self._phone))
        mix = [
            audio.mix_frames(
                self._caller[i] if i < len(self._caller) else audio.SILENCE_FRAME,
                self._phone[i] if i < len(self._phone) else audio.SILENCE_FRAME,
            )
            for i in range(n)
        ]
        audio.wav_write_frames(self._dir / "mix.wav", mix)
        return self._dir


def sweep_retention(recordings_root: Path, max_age_days: int = 90,
                    now: float | None = None) -> int:
    if not recordings_root.exists():
        return 0
    cutoff = (now or time.time()) - max_age_days * 86400
    deleted = 0
    for child in recordings_root.iterdir():
        if child.is_dir() and child.stat().st_mtime < cutoff:
            shutil.rmtree(child)
            deleted += 1
    return deleted


def free_space_gib(path: Path) -> float:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free / (1024 ** 3)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd hotline/server; python -m pytest tests/test_recording.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add hotline/server/hotline/recording.py hotline/server/tests/test_recording.py
git commit -m "feat(hotline): call recorder (raw legs + mix) and 90-day retention sweep"
```

---

### Task 8: Call session state machine (`call.py`)

The heart. Owns one call: jitter buffer, 20 ms pump, timer, T−10 beep, time's-up
tone, recorder, events. Phone-leg transport is injected so tests (and the Task 13
e2e) run without Asterisk.

**Files:**
- Create: `hotline/server/hotline/call.py`
- Test: `hotline/server/tests/test_call.py`

**Interfaces:**
- Consumes: `JitterBuffer`, `CallRecorder`, `EventBus`, `audio.*`.
- Produces: `class PhoneLeg(Protocol)` — `async def ring(caller_name: str) -> None`, `async def hangup() -> None`, `async def send_frame(frame: bytes) -> None`. Implementations report answer/hangup/audio by calling the session methods below.
- Produces: `class CallSession(call_id: str, caller_label: str, seconds: float, phone: PhoneLeg, bus: EventBus, recorder: CallRecorder, send_to_caller: Callable[[bytes], Awaitable[None]], grace_s: float = 10.0)` with:
  - `async start()` → publishes `call_ringing`, `await phone.ring(...)`
  - `def on_phone_answered()` → publishes `call_active`, starts pump + timer
  - `def on_phone_frame(frame: bytes)` → forward to caller + recorder
  - `def on_phone_hungup()` → end (`outcome="completed"` if timer ran out earlier, else `"dropped"`; Plan 1 keeps it simple: `"completed"` after answer, `"dropped"` before)
  - `def on_caller_frame(frame: bytes)` → jitter push
  - `def on_caller_lost()` → grace timer → hangup (`outcome="dropped"`)
  - `async def end(outcome: str)` idempotent → stops pump, closes recorder, publishes `call_ended`
  - `done: asyncio.Event`, `outcome: str | None`, `seconds_used: float`
- Timing behavior: T−10 s publishes `call_warning` + injects two 200 ms 440 Hz beeps into **both** directions; at T0 injects a 400 ms 300 Hz time's-up tone then `phone.hangup()`. For `seconds < 10` (tests), the warning fires immediately after answer.

- [ ] **Step 1: Write failing tests**

`hotline/server/tests/test_call.py`:
```python
from __future__ import annotations

import asyncio

from hotline import audio
from hotline.call import CallSession
from hotline.events import EventBus
from hotline.recording import CallRecorder


class FakePhone:
    def __init__(self) -> None:
        self.rang = asyncio.Event()
        self.hungup = asyncio.Event()
        self.frames: list[bytes] = []
        self.session: CallSession | None = None

    async def ring(self, caller_name: str) -> None:
        self.rang.set()

    async def hangup(self) -> None:
        self.hungup.set()
        if self.session:
            self.session.on_phone_hungup()

    async def send_frame(self, frame: bytes) -> None:
        self.frames.append(frame)


def make_session(tmp_path, seconds: float, grace_s: float = 0.2):
    bus = EventBus(delay_n=0.01)
    phone = FakePhone()
    to_caller: list[bytes] = []

    async def send_to_caller(f: bytes) -> None:
        to_caller.append(f)

    sess = CallSession(
        call_id="c1", caller_label="tester", seconds=seconds, phone=phone,
        bus=bus, recorder=CallRecorder(tmp_path / "c1"),
        send_to_caller=send_to_caller, grace_s=grace_s)
    phone.session = sess
    return bus, phone, sess, to_caller


async def drain(q: asyncio.Queue) -> list[str]:
    out = []
    while not q.empty():
        out.append((await q.get())["type"])
    return out


async def test_happy_path_expires_and_hangs_up(tmp_path):
    bus, phone, sess, to_caller = make_session(tmp_path, seconds=0.5)
    await bus.start()
    rt = bus.subscribe("rt")
    await sess.start()
    await asyncio.wait_for(phone.rang.wait(), 1)
    sess.on_phone_answered()
    # stream some caller audio while active
    for f in audio.tone_frames(440.0, 100):
        sess.on_caller_frame(f)
    phone.session = phone.session  # (fake wires hangup->on_phone_hungup)
    await asyncio.wait_for(sess.done.wait(), 5)
    assert phone.hungup.is_set()
    assert sess.outcome == "completed"
    types = await drain(rt)
    assert types[:2] == ["call_ringing", "call_active"]
    assert "call_warning" in types and types[-1] == "call_ended"
    # phone received jitter-buffered audio (incl. silence + beeps)
    assert len(phone.frames) > 0
    rec = tmp_path / "c1"
    assert (rec / "caller.wav").exists() and (rec / "mix.wav").exists()
    await bus.stop()


async def test_caller_lost_grace_then_drop(tmp_path):
    bus, phone, sess, _ = make_session(tmp_path, seconds=30, grace_s=0.1)
    await bus.start()
    await sess.start()
    sess.on_phone_answered()
    sess.on_caller_lost()
    await asyncio.wait_for(sess.done.wait(), 2)
    assert sess.outcome == "dropped" and phone.hungup.is_set()
    await bus.stop()


async def test_phone_frames_reach_caller_and_recorder(tmp_path):
    bus, phone, sess, to_caller = make_session(tmp_path, seconds=30)
    await bus.start()
    await sess.start()
    sess.on_phone_answered()
    sess.on_phone_frame(audio.SILENCE_FRAME)
    await asyncio.sleep(0.05)
    assert to_caller and to_caller[0] == audio.SILENCE_FRAME
    await sess.end("test")
    assert audio.wav_read_frames(tmp_path / "c1" / "phone.wav") == [audio.SILENCE_FRAME]
    await bus.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd hotline/server; python -m pytest tests/test_call.py -q`
Expected: FAIL — `No module named 'hotline.call'`

- [ ] **Step 3: Implement**

`hotline/server/hotline/call.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd hotline/server; python -m pytest tests/test_call.py -q`
Expected: `3 passed`

- [ ] **Step 5: Run the whole suite (regression gate)**

Run: `cd hotline/server; python -m pytest -q`
Expected: all tests from Tasks 1–8 pass.

- [ ] **Step 6: Commit**

```bash
git add hotline/server/hotline/call.py hotline/server/tests/test_call.py
git commit -m "feat(hotline): CallSession — pump, timer, T-10 beep, grace, recording"
```

---

### Task 9: ARI client (`ari.py`)

Minimal Asterisk REST Interface client. The composition (Task 11) uses it to build the
real `PhoneLeg`. Tested against a fake ARI server (aiohttp app in the test).

**Files:**
- Create: `hotline/server/hotline/ari.py`
- Test: `hotline/server/tests/test_ari.py`

**Interfaces:**
- Consumes: `Config` fields `ari_url`, `ari_user`, `ari_password`.
- Produces: `class AriClient(base_url: str, user: str, password: str, app: str = "pork")` —
  - `async connect()` — opens the events WS `GET {base}/ari/events?app={app}&api_key={user}:{password}` and dispatches JSON events to `on_event(cb: Callable[[dict], None])` subscribers; `async close()`.
  - `async originate_phone(caller_id: str, channel_var_uuid: str) -> str` — `POST /ari/channels` json `{"endpoint": "PJSIP/ata", "app": app, "callerId": caller_id, "appArgs": "phone", "variables": {"PORK_UUID": channel_var_uuid}}`, returns channel id.
  - `async external_media(audiosocket_uuid: str, host: str) -> str` — `POST /ari/channels/externalMedia` json `{"app": app, "external_host": host, "encapsulation": "audiosocket", "transport": "tcp", "format": "slin", "data": audiosocket_uuid}`, returns channel id. (⚠ Param set verified against installed Asterisk in Task 15; fallback documented in `asterisk/README.md` is the dialplan `AudioSocket()` route.)
  - `async bridge(channel_ids: list[str]) -> str` — `POST /ari/bridges` `{"type": "mixing"}` + `POST /ari/bridges/{id}/addChannel?channel=a,b`.
  - `async hangup(channel_id: str)` — `DELETE /ari/channels/{channel_id}` (ignore 404).

- [ ] **Step 1: Write failing tests**

`hotline/server/tests/test_ari.py`:
```python
from __future__ import annotations

import asyncio
import json

from aiohttp import web
from aiohttp.test_utils import TestServer

from hotline.ari import AriClient


def make_fake_ari(record: dict):
    app = web.Application()

    async def events_ws(request: web.Request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        record["ws_query"] = dict(request.query)
        await ws.send_str(json.dumps({"type": "StasisStart",
                                      "channel": {"id": "ch-1"}}))
        async for _ in ws:
            pass
        return ws

    async def channels(request: web.Request):
        record["originate"] = await request.json()
        return web.json_response({"id": "ch-1"})

    async def external(request: web.Request):
        record["external"] = await request.json()
        return web.json_response({"id": "ch-em"})

    async def bridges(request: web.Request):
        return web.json_response({"id": "br-1"})

    async def add_channel(request: web.Request):
        record["bridged"] = request.query["channel"]
        return web.Response(status=204)

    async def hangup(request: web.Request):
        record["hungup"] = request.match_info["cid"]
        return web.Response(status=204)

    app.router.add_get("/ari/events", events_ws)
    app.router.add_post("/ari/channels", channels)
    app.router.add_post("/ari/channels/externalMedia", external)
    app.router.add_post("/ari/bridges", bridges)
    app.router.add_post("/ari/bridges/{bid}/addChannel", add_channel)
    app.router.add_delete("/ari/channels/{cid}", hangup)
    return app


async def test_ari_flow():
    record: dict = {}
    server = TestServer(make_fake_ari(record))
    await server.start_server()
    base = f"http://127.0.0.1:{server.port}"
    events: list[dict] = []

    client = AriClient(base, "hotline", "pw")
    client.on_event(events.append)
    await client.connect()
    await asyncio.sleep(0.05)
    assert record["ws_query"]["app"] == "pork"
    assert events and events[0]["type"] == "StasisStart"

    ch = await client.originate_phone("PORK FAN", "u-1")
    assert ch == "ch-1"
    assert record["originate"]["endpoint"] == "PJSIP/ata"
    assert record["originate"]["variables"]["PORK_UUID"] == "u-1"

    em = await client.external_media("u-1", "127.0.0.1:9101")
    assert em == "ch-em"
    assert record["external"]["encapsulation"] == "audiosocket"
    assert record["external"]["data"] == "u-1"

    br = await client.bridge(["ch-1", "ch-em"])
    assert br == "br-1" and record["bridged"] == "ch-1,ch-em"

    await client.hangup("ch-1")
    assert record["hungup"] == "ch-1"
    await client.close()
    await server.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd hotline/server; python -m pytest tests/test_ari.py -q`
Expected: FAIL — `No module named 'hotline.ari'`

- [ ] **Step 3: Implement**

`hotline/server/hotline/ari.py`:
```python
from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Callable, Optional

import aiohttp


class AriClient:
    def __init__(self, base_url: str, user: str, password: str,
                 app: str = "pork") -> None:
        self._base = base_url.rstrip("/")
        self._auth = aiohttp.BasicAuth(user, password)
        self._api_key = f"{user}:{password}"
        self.app = app
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._listeners: list[Callable[[dict], None]] = []

    def on_event(self, cb: Callable[[dict], None]) -> None:
        self._listeners.append(cb)

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession(auth=self._auth)
        url = f"{self._base}/ari/events?app={self.app}&api_key={self._api_key}"
        ws = await self._session.ws_connect(url)
        self._ws_task = asyncio.create_task(self._listen(ws))

    async def _listen(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                event = json.loads(msg.data)
                for cb in self._listeners:
                    cb(event)

    async def close(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ws_task
        if self._session:
            await self._session.close()

    async def _post(self, path: str, payload: dict) -> dict:
        assert self._session is not None
        async with self._session.post(self._base + path, json=payload) as resp:
            resp.raise_for_status()
            return await resp.json() if resp.status != 204 else {}

    async def originate_phone(self, caller_id: str, channel_var_uuid: str) -> str:
        data = await self._post("/ari/channels", {
            "endpoint": "PJSIP/ata", "app": self.app, "callerId": caller_id,
            "appArgs": "phone", "variables": {"PORK_UUID": channel_var_uuid}})
        return data["id"]

    async def external_media(self, audiosocket_uuid: str, host: str) -> str:
        data = await self._post("/ari/channels/externalMedia", {
            "app": self.app, "external_host": host,
            "encapsulation": "audiosocket", "transport": "tcp",
            "format": "slin", "data": audiosocket_uuid})
        return data["id"]

    async def bridge(self, channel_ids: list[str]) -> str:
        data = await self._post("/ari/bridges", {"type": "mixing"})
        bridge_id = data["id"]
        assert self._session is not None
        async with self._session.post(
            f"{self._base}/ari/bridges/{bridge_id}/addChannel",
            params={"channel": ",".join(channel_ids)},
        ) as resp:
            resp.raise_for_status()
        return bridge_id

    async def hangup(self, channel_id: str) -> None:
        assert self._session is not None
        async with self._session.delete(
            f"{self._base}/ari/channels/{channel_id}"
        ) as resp:
            if resp.status not in (204, 404):
                resp.raise_for_status()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd hotline/server; python -m pytest tests/test_ari.py -q`
Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add hotline/server/hotline/ari.py hotline/server/tests/test_ari.py
git commit -m "feat(hotline): minimal ARI client (originate/externalMedia/bridge/hangup)"
```

---

### Task 10: Browser test page + audio worklets (static assets)

Bare page for Phase-1 verification (the real `/phone` page is Plan 2 and will reuse the
worklets). Browser does all resampling: mic 48 kHz → low-pass → decimate ×6 → int16
320-byte frames over WS; downlink reverses it. Echo mode (`HOTLINE_ECHO=1`, wired in
Task 11) lets Paul hear himself round-trip with zero telephony.

**Files:**
- Create: `hotline/server/hotline/static/test.html`, `hotline/server/hotline/static/capture-worklet.js`, `hotline/server/hotline/static/playback-worklet.js`

**Interfaces:**
- Produces: page connects `WS {origin}/ws/audio?token=<admin token from the page's input>`; sends binary 320-byte frames; on binary message, plays it. Buttons: CONNECT / RING PHONE (POST `/admin/test-ring?token=...`) / HANG UP (POST `/admin/hangup?token=...`). A `<pre>` log tails `WS /ws/events?feed=rt&token=...`.
- Consumes (from Task 11): those endpoints.

- [ ] **Step 1: Write the worklets and page**

`hotline/server/hotline/static/capture-worklet.js`:
```js
// 48 kHz float in -> every 6th sample -> 160-sample int16 chunks (20 ms @ 8 kHz).
// A BiquadFilter (3.4 kHz lowpass) runs upstream in the graph as the anti-alias filter.
class CaptureWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Int16Array(160);
    this.n = 0;
    this.phase = 0; // decimation phase across process() calls
  }
  process(inputs) {
    const ch = inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      if (this.phase === 0) {
        const s = Math.max(-1, Math.min(1, ch[i]));
        this.buf[this.n++] = (s * 32767) | 0;
        if (this.n === 160) {
          this.port.postMessage(this.buf.buffer.slice(0));
          this.n = 0;
        }
      }
      this.phase = (this.phase + 1) % 6;
    }
    return true;
  }
}
registerProcessor("capture-worklet", CaptureWorklet);
```

`hotline/server/hotline/static/playback-worklet.js`:
```js
// int16 8 kHz frames in (via port) -> sample-repeat x6 to 48 kHz out.
// A downstream BiquadFilter (3.4 kHz lowpass) smooths the steps.
class PlaybackWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.cur = null;
    this.idx = 0;
    this.rep = 0;
    this.port.onmessage = (e) => {
      this.queue.push(new Int16Array(e.data));
      if (this.queue.length > 25) this.queue.shift(); // cap ~0.5 s
    };
  }
  process(_inputs, outputs) {
    const out = outputs[0][0];
    for (let i = 0; i < out.length; i++) {
      if (!this.cur || this.idx >= this.cur.length) {
        this.cur = this.queue.shift() || null;
        this.idx = 0;
      }
      out[i] = this.cur ? this.cur[this.idx] / 32768 : 0;
      this.rep = (this.rep + 1) % 6;
      if (this.rep === 0 && this.cur) this.idx++;
    }
    return true;
  }
}
registerProcessor("playback-worklet", PlaybackWorklet);
```

`hotline/server/hotline/static/test.html`:
```html
<!doctype html>
<meta charset="utf-8">
<title>PORK PHONE — bench test page</title>
<style>
  body { font-family: monospace; max-width: 640px; margin: 2rem auto; }
  button { font: inherit; padding: .5rem 1rem; margin-right: .5rem; }
  #log { background: #111; color: #0f0; padding: 1rem; height: 16rem;
         overflow-y: scroll; white-space: pre-wrap; }
</style>
<h1>THE PORK PHONE — bench test</h1>
<p>token <input id="token" value="dev-token">
   <button id="connect">CONNECT MIC</button>
   <button id="ring" disabled>RING PHONE</button>
   <button id="hang" disabled>HANG UP</button></p>
<pre id="log"></pre>
<script>
const log = (m) => {
  const el = document.getElementById("log");
  el.textContent += m + "\n"; el.scrollTop = el.scrollHeight;
};
let audioWs = null;
document.getElementById("connect").onclick = async () => {
  const token = document.getElementById("token").value;
  const ctx = new AudioContext({ sampleRate: 48000 });
  await ctx.audioWorklet.addModule("/static/capture-worklet.js");
  await ctx.audioWorklet.addModule("/static/playback-worklet.js");
  const mic = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true } });
  const src = ctx.createMediaStreamSource(mic);
  const lp1 = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 3400 });
  const lp2 = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 3400 });
  const cap = new AudioWorkletNode(ctx, "capture-worklet");
  src.connect(lp1).connect(lp2).connect(cap);
  const play = new AudioWorkletNode(ctx, "playback-worklet");
  const lp3 = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 3400 });
  play.connect(lp3).connect(ctx.destination);

  const proto = location.protocol === "https:" ? "wss" : "ws";
  audioWs = new WebSocket(
    `${proto}://${location.host}/ws/audio?token=${encodeURIComponent(token)}`);
  audioWs.binaryType = "arraybuffer";
  audioWs.onopen = () => { log("audio WS open"); };
  audioWs.onclose = (e) => log(`audio WS closed (${e.code})`);
  audioWs.onmessage = (e) => play.port.postMessage(e.data);
  cap.port.onmessage = (e) => {
    if (audioWs && audioWs.readyState === 1) audioWs.send(e.data);
  };

  const evWs = new WebSocket(
    `${proto}://${location.host}/ws/events?feed=rt&token=${encodeURIComponent(token)}`);
  evWs.onmessage = (e) => log("event " + e.data);
  document.getElementById("ring").disabled = false;
  document.getElementById("hang").disabled = false;
};
document.getElementById("ring").onclick = async () => {
  const token = document.getElementById("token").value;
  const r = await fetch(`/admin/test-ring?token=${encodeURIComponent(token)}`,
                        { method: "POST" });
  log("test-ring -> " + r.status);
};
document.getElementById("hang").onclick = async () => {
  const token = document.getElementById("token").value;
  const r = await fetch(`/admin/hangup?token=${encodeURIComponent(token)}`,
                        { method: "POST" });
  log("hangup -> " + r.status);
};
</script>
```

- [ ] **Step 2: Verify assets are syntactically sound**

Run: `node --check hotline/server/hotline/static/capture-worklet.js; node --check hotline/server/hotline/static/playback-worklet.js`
Expected: no output (both parse). (If node is unavailable, skip — Task 11's manual echo
test exercises them for real.)

- [ ] **Step 3: Commit**

```bash
git add hotline/server/hotline/static
git commit -m "feat(hotline): bench test page + 8kHz capture/playback worklets"
```

---

### Task 11: Controller + HTTP/WS surface (`controller.py`, extend `http.py`)

Single-call-slot controller wiring everything: audio WS ⇄ CallSession ⇄ AudioSocket,
admin endpoints, events WS, echo mode, lines-open guard.

**Files:**
- Create: `hotline/server/hotline/controller.py`
- Modify: `hotline/server/hotline/http.py` (add WS + admin routes + static)
- Test: `hotline/server/tests/test_controller_http.py`

**Interfaces:**
- Consumes: everything produced by Tasks 1–9.
- Produces: `class Controller(cfg: Config, bus: EventBus, db: Db, phone_leg_factory: Callable[[CallSession], PhoneLeg])` —
  - `async start()` / `async stop()` (starts `AudioSocketServer` on `cfg.audiosocket_port`, retention sweep, publishes `lines_state` closed).
  - `async attach_caller_ws(send: Callable[[bytes], Awaitable[None]]) -> None` / `def detach_caller_ws()` / `def on_caller_audio(frame: bytes)` — one caller at a time (409 for a second).
  - `async test_ring(seconds: float = 60) -> str` — guards: caller attached, no active call, `free_space_gib(recordings) >= 1.0`; creates `calls` row, builds `CallSession` (uuid4 dashed-string id — dashed canonical form — Asterisk res_audiosocket parses with libuuid, which rejects dash-less hex), `phone_leg_factory` builds the leg, starts it; returns call id. In `cfg.echo_mode` the factory is replaced by `EchoPhoneLeg` (below).
  - `async hangup_active() -> bool`.
  - `def on_audiosocket_session(sess) -> bool` — accepts only if `str(sess.uuid) == active call's id`; wires `sess.on_audio -> call.on_phone_frame`, session close → `call.on_phone_hungup`, and the phone leg's `send_frame` → `sess.send_audio`.
- Produces: `class EchoPhoneLeg(PhoneLeg)` — `ring()` immediately calls `session.on_phone_answered()`; `send_frame(f)` loops the frame back via `session.on_phone_frame(f)`; `hangup()` no-ops. (Dev/e2e without Asterisk.)
- Produces in `http.py`: `GET /ws/audio?token=` (binary frames in/out) · `GET /ws/events?feed=rt|delayed&token=` (JSON out) · `POST /admin/test-ring?token=&seconds=` → `{"call_id": ...}` · `POST /admin/hangup?token=` · `GET /static/*` + `GET /test` (serves `test.html`). All token checks constant-time (`hmac.compare_digest`), 401 on mismatch, 409 on slot conflicts.

- [ ] **Step 1: Write failing tests**

`hotline/server/tests/test_controller_http.py`:
```python
from __future__ import annotations

import asyncio

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

from hotline import audio
from hotline.config import Config
from hotline.controller import Controller
from hotline.db import Db
from hotline.events import EventBus
from hotline.http import make_app


async def make_stack(tmp_path, unused_tcp_port, echo=True):
    cfg = Config.from_env({
        "HOTLINE_ENV": "dev", "HOTLINE_DATA_DIR": str(tmp_path),
        "HOTLINE_AUDIOSOCKET_PORT": str(unused_tcp_port),
        "HOTLINE_ECHO": "1" if echo else "",
    })
    bus = EventBus(delay_n=0.05)
    await bus.start()
    db = Db(tmp_path / "h.db"); db.init()
    ctl = Controller(cfg, bus, db, phone_leg_factory=None)
    await ctl.start()
    client = TestClient(TestServer(make_app(cfg, ctl)))
    await client.start_server()
    return cfg, bus, db, ctl, client


async def test_auth_rejected(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    resp = await client.post("/admin/test-ring?token=wrong")
    assert resp.status == 401
    await client.close(); await ctl.stop(); await bus.stop(); db.close()


async def test_ring_requires_caller(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    resp = await client.post("/admin/test-ring?token=dev-token")
    assert resp.status == 409  # no caller connected
    await client.close(); await ctl.stop(); await bus.stop(); db.close()


async def test_echo_call_roundtrip(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    ws = await client.ws_connect("/ws/audio?token=dev-token")
    ev = await client.ws_connect("/ws/events?feed=rt&token=dev-token")

    resp = await client.post("/admin/test-ring?token=dev-token&seconds=1")
    assert resp.status == 200
    call_id = (await resp.json())["call_id"]

    # stream 500 ms of tone; echo leg loops it straight back
    frames = audio.tone_frames(440.0, 500)
    got = bytearray()

    async def sender():
        for f in frames:
            await ws.send_bytes(f)
            await asyncio.sleep(0.02)

    async def receiver():
        while len(got) < 10 * audio.FRAME_BYTES:
            msg = await asyncio.wait_for(ws.receive(), 5)
            if msg.type == aiohttp.WSMsgType.BINARY:
                got.extend(msg.data)

    await asyncio.gather(sender(), receiver())
    assert len(got) >= 10 * audio.FRAME_BYTES  # audio came back

    # events observed
    seen = set()
    while len(seen) < 2:
        msg = await asyncio.wait_for(ev.receive_json(), 5)
        seen.add(msg["type"])
    assert {"call_ringing", "call_active"} <= seen

    # timer (1 s) expires the call; DB row completed
    await asyncio.sleep(2.0)
    row = db._conn.execute(
        "SELECT outcome FROM calls WHERE call_id=?", (call_id,)).fetchone()
    assert row and row[0] == "completed"

    await ws.close(); await ev.close()
    await client.close(); await ctl.stop(); await bus.stop(); db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd hotline/server; python -m pytest tests/test_controller_http.py -q`
Expected: FAIL — `No module named 'hotline.controller'`

- [ ] **Step 3: Implement controller**

`hotline/server/hotline/controller.py`:
```python
from __future__ import annotations

import asyncio
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
        return None

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

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        await self._audiosocket.start()
        sweep_retention(self._recordings_root())
        self.bus.publish({"type": "lines_state", "open": False})

    async def stop(self) -> None:
        if self._call:
            await self._call.end("dropped")
        await self._audiosocket.stop()

    def _recordings_root(self) -> Path:
        return self.cfg.data_dir / "recordings"

    # -- caller WS -----------------------------------------------------------
    async def attach_caller_ws(
            self, send: Callable[[bytes], Awaitable[None]]) -> None:
        if self._caller_send is not None:
            raise RuntimeError("caller slot busy")
        self._caller_send = send

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
        call_id = str(uuid.uuid4())
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
        asyncio.create_task(self._reap(call))
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
```

- [ ] **Step 4: Extend `http.py`**

Replace `hotline/server/hotline/http.py` with:
```python
from __future__ import annotations

import hmac
from pathlib import Path

import aiohttp
from aiohttp import web

from .config import Config

STATIC_DIR = Path(__file__).parent / "static"


def _authed(request: web.Request) -> bool:
    cfg: Config = request.app["cfg"]
    token = request.query.get("token", "")
    return hmac.compare_digest(token, cfg.admin_token)


async def _healthz(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _test_page(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "test.html")


async def _test_ring(request: web.Request) -> web.Response:
    if not _authed(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    controller = request.app["controller"]
    seconds = float(request.query.get("seconds", "60"))
    try:
        call_id = await controller.test_ring(seconds)
    except RuntimeError as e:
        return web.json_response({"error": str(e)}, status=409)
    return web.json_response({"call_id": call_id})


async def _hangup(request: web.Request) -> web.Response:
    if not _authed(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    ok = await request.app["controller"].hangup_active()
    return web.json_response({"hungup": ok})


async def _ws_audio(request: web.Request) -> web.WebSocketResponse:
    if not _authed(request):
        raise web.HTTPUnauthorized()
    controller = request.app["controller"]
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    async def send(frame: bytes) -> None:
        if not ws.closed:
            await ws.send_bytes(frame)

    try:
        await controller.attach_caller_ws(send)
    except RuntimeError:
        await ws.close(code=4009, message=b"caller slot busy")
        return ws
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.BINARY:
                controller.on_caller_audio(msg.data)
    finally:
        controller.detach_caller_ws()
    return ws


async def _ws_events(request: web.Request) -> web.WebSocketResponse:
    if not _authed(request):
        raise web.HTTPUnauthorized()
    feed = request.query.get("feed", "rt")
    if feed not in ("rt", "delayed"):
        raise web.HTTPBadRequest()
    bus = request.app["bus"]
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    q = bus.subscribe(feed)
    try:
        while not ws.closed:
            event = await q.get()
            await ws.send_json(event)
    except (ConnectionError, RuntimeError):
        pass
    finally:
        bus.unsubscribe(feed, q)
    return ws


def make_app(cfg: Config, controller=None) -> web.Application:
    app = web.Application()
    app["cfg"] = cfg
    app["controller"] = controller
    app["bus"] = controller.bus if controller else None
    app.router.add_get("/healthz", _healthz)
    app.router.add_get("/test", _test_page)
    app.router.add_static("/static/", STATIC_DIR)
    app.router.add_post("/admin/test-ring", _test_ring)
    app.router.add_post("/admin/hangup", _hangup)
    app.router.add_get("/ws/audio", _ws_audio)
    app.router.add_get("/ws/events", _ws_events)
    return app
```

Note: `tests/test_http.py` from Task 1 still passes — `make_app(cfg)` with no controller
serves `/healthz` (WS/admin routes 500 on a None controller only if called, which that
test doesn't).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd hotline/server; python -m pytest tests/test_controller_http.py tests/test_http.py -q`
Expected: `4 passed`

- [ ] **Step 6: Run the whole suite**

Run: `cd hotline/server; python -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add hotline/server/hotline/controller.py hotline/server/hotline/http.py hotline/server/tests/test_controller_http.py
git commit -m "feat(hotline): controller + HTTP/WS surface with echo mode"
```

---

### Task 12: `__main__` composition + graceful shutdown

**Files:**
- Create: `hotline/server/hotline/__main__.py`
- Test: `hotline/server/tests/test_main.py`

**Interfaces:**
- Consumes: everything.
- Produces: `async def build_and_run(cfg: Config, stop: asyncio.Event) -> None` — constructs Db/EventBus/Controller (echo leg when `cfg.echo_mode`, else the real ARI-backed leg, Task 15 wires its config), binds HTTP on `127.0.0.1:cfg.http_port`, waits on `stop`, tears down cleanly. `python -m hotline` runs it with SIGINT/SIGTERM → `stop`.
- The real phone leg: `class AriPhoneLeg(PhoneLeg)` defined here — `ring()` = `originate_phone()` then on `StasisStart` for its channel: `external_media(call_id, f"127.0.0.1:{cfg.audiosocket_port}")` + `bridge()`, and `session.on_phone_answered()`; ARI `StasisEnd`/`ChannelDestroyed` for its channel → `session.on_phone_hungup()`; `send_frame` is a no-op passthrough (phone audio flows via the AudioSocket session the controller wires — `Controller._on_audiosocket_session` remains the audio path; `AriPhoneLeg.send_frame` forwards to the controller's active `AudioSocketSession` via a setter `set_audio_sender(cb)` the controller calls).

- [ ] **Step 1: Write failing test**

`hotline/server/tests/test_main.py`:
```python
from __future__ import annotations

import asyncio

import aiohttp

from hotline.__main__ import build_and_run
from hotline.config import Config


async def test_boots_serves_and_stops(tmp_path, unused_tcp_port_factory):
    http_port = unused_tcp_port_factory()
    cfg = Config.from_env({
        "HOTLINE_ENV": "dev", "HOTLINE_DATA_DIR": str(tmp_path),
        "HOTLINE_HTTP_PORT": str(http_port),
        "HOTLINE_AUDIOSOCKET_PORT": str(unused_tcp_port_factory()),
        "HOTLINE_ECHO": "1",
    })
    stop = asyncio.Event()
    task = asyncio.create_task(build_and_run(cfg, stop))
    await asyncio.sleep(0.3)
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{http_port}/healthz") as resp:
            assert resp.status == 200
    stop.set()
    await asyncio.wait_for(task, 5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hotline/server; python -m pytest tests/test_main.py -q`
Expected: FAIL — no `build_and_run`.

- [ ] **Step 3: Implement**

`hotline/server/hotline/__main__.py`:
```python
from __future__ import annotations

import asyncio
import contextlib
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


class AriPhoneLeg:
    """Real phone leg: rings PJSIP/ata via ARI; audio flows over the
    controller's AudioSocket session (Asterisk externalMedia dials us)."""

    def __init__(self, session: CallSession, ari: AriClient,
                 audiosocket_port: int) -> None:
        self._session = session
        self._ari = ari
        self._port = audiosocket_port
        self._channel_id: Optional[str] = None
        self._send_audio: Optional[Callable[[bytes], Awaitable[None]]] = None
        ari.on_event(self._on_ari_event)

    def set_audio_sender(self, cb: Callable[[bytes], Awaitable[None]]) -> None:
        self._send_audio = cb

    async def ring(self, caller_name: str) -> None:
        self._channel_id = await self._ari.originate_phone(
            caller_name, self._session.call_id)

    def _on_ari_event(self, event: dict) -> None:
        ch = (event.get("channel") or {}).get("id")
        if ch != self._channel_id:
            return
        if event.get("type") == "StasisStart":
            asyncio.create_task(self._on_answered())
        elif event.get("type") in ("StasisEnd", "ChannelDestroyed"):
            self._session.on_phone_hungup()

    async def _on_answered(self) -> None:
        em = await self._ari.external_media(
            self._session.call_id, f"127.0.0.1:{self._port}")
        assert self._channel_id is not None
        await self._ari.bridge([self._channel_id, em])
        self._session.on_phone_answered()

    async def hangup(self) -> None:
        if self._channel_id:
            with contextlib.suppress(Exception):
                await self._ari.hangup(self._channel_id)

    async def send_frame(self, frame: bytes) -> None:
        if self._send_audio:
            await self._send_audio(frame)


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
        await ari.connect()

        def factory(session: CallSession) -> AriPhoneLeg:
            return AriPhoneLeg(session, ari, cfg.audiosocket_port)

    controller = Controller(cfg, bus, db, phone_leg_factory=factory)
    await controller.start()

    app = make_app(cfg, controller)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", cfg.http_port)
    await site.start()

    await stop.wait()

    await runner.cleanup()
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd hotline/server; python -m pytest tests/test_main.py -q`
Expected: `1 passed`

- [ ] **Step 5: Manual echo smoke (browser, no telephony)**

Run: `cd hotline/server; $env:HOTLINE_ENV="dev"; $env:HOTLINE_DATA_DIR="./devdata"; $env:HOTLINE_ECHO="1"; python -m hotline`
Then open `http://127.0.0.1:9100/test` in a browser → CONNECT MIC → RING PHONE → speak.
Expected: you hear yourself back at phone quality (~100–300 ms behind); event log shows
`call_ringing`, `call_active`, then `call_warning`/`call_ended` after the 60 s expires or
HANG UP. This is the worklets + jitter buffer + pump verified end-to-end by ear.

- [ ] **Step 6: Commit**

```bash
git add hotline/server/hotline/__main__.py hotline/server/tests/test_main.py
git commit -m "feat(hotline): composition root, AriPhoneLeg, graceful shutdown"
```

---

### Task 13: End-to-end WAV pipeline test (fake Asterisk)

The money test: a WS "caller" streams a WAV in; a fake Asterisk AudioSocket client (what
the real Asterisk is on the wire) streams a different WAV back; assert both directions
arrive, recordings + DB + events are right. Zero telephony, zero hardware.

**Files:**
- Test: `hotline/server/tests/test_e2e_wav.py`

**Interfaces:**
- Consumes: `build_and_run` from Task 12 (echo off — the test needs the AudioSocket path); Controller accepts the AudioSocket session by call-id UUID; but with echo off the factory is ARI-backed. For the e2e we need a phone leg that rings without ARI: reuse `EchoPhoneLeg`? No — the test must exercise AudioSocket. Solution: the test builds the stack **directly** (like `make_stack` in Task 11's test) with a custom `FakeAtaPhoneLeg` factory that (a) marks answered on `ring()`, (b) opens a real TCP AudioSocket client connection with the call's UUID, streaming `phone.wav` frames and collecting what the server sends. `send_frame` forwards to the controller's live `AudioSocketSession` — which is exactly how `AriPhoneLeg` works via `set_audio_sender`; the fake does the same wiring inline.

- [ ] **Step 1: Write the test**

`hotline/server/tests/test_e2e_wav.py`:
```python
from __future__ import annotations

import asyncio
import uuid as uuidlib

from aiohttp.test_utils import TestClient, TestServer

from hotline import audio, audiosocket as aus
from hotline.config import Config
from hotline.controller import Controller
from hotline.db import Db
from hotline.events import EventBus
from hotline.http import make_app


async def test_full_pipeline_wav_both_directions(tmp_path, unused_tcp_port):
    cfg = Config.from_env({
        "HOTLINE_ENV": "dev", "HOTLINE_DATA_DIR": str(tmp_path),
        "HOTLINE_AUDIOSOCKET_PORT": str(unused_tcp_port),
    })
    bus = EventBus(delay_n=0.05); await bus.start()
    db = Db(tmp_path / "h.db"); db.init()

    caller_wav = audio.tone_frames(440.0, 400)   # what the "viewer" says
    phone_wav = audio.tone_frames(220.0, 400)    # what "Paul" says
    phone_got: list[bytes] = []
    fake_done = asyncio.Event()

    class FakeAtaPhoneLeg:
        def __init__(self, session) -> None:
            self._session = session
            self._writer = None

        async def ring(self, caller_name: str) -> None:
            # "Paul lifts the horn": connect AudioSocket with the call uuid
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", cfg.audiosocket_port)
            self._writer = writer
            u = uuidlib.UUID(hex=self._session.call_id)
            writer.write(aus.encode_frame(aus.KIND_UUID, u.bytes))
            await writer.drain()
            self._session.on_phone_answered()
            asyncio.get_running_loop().create_task(self._speak_and_listen(reader))

        async def _speak_and_listen(self, reader) -> None:
            async def speak():
                for f in phone_wav:
                    self._writer.write(aus.encode_frame(aus.KIND_AUDIO, f))
                    await self._writer.drain()
                    await asyncio.sleep(0.02)

            async def listen():
                while len(phone_got) < 15:
                    kind, payload = await aus.read_frame(reader)
                    if kind == aus.KIND_AUDIO:
                        phone_got.append(payload)

            await asyncio.gather(speak(), listen())
            fake_done.set()

        async def hangup(self) -> None:
            return None

        async def send_frame(self, frame: bytes) -> None:
            return None  # audio to the phone flows via the AudioSocket session
            # NOTE: in this stack the controller does NOT auto-wire send_frame;
            # the CallSession pump calls phone.send_frame — so the test asserts
            # the *AudioSocket* server->client path separately below.

    ctl = Controller(cfg, bus, db, phone_leg_factory=FakeAtaPhoneLeg)
    # force real factory even though env token says dev
    ctl.cfg = cfg
    await ctl.start()
    client = TestClient(TestServer(make_app(cfg, ctl)))
    await client.start_server()

    ws = await client.ws_connect("/ws/audio?token=dev-token")
    resp = await client.post("/admin/test-ring?token=dev-token&seconds=30")
    assert resp.status == 200
    call_id = (await resp.json())["call_id"]

    caller_got = bytearray()

    async def send_caller():
        for f in caller_wav:
            await ws.send_bytes(f)
            await asyncio.sleep(0.02)

    async def recv_caller():
        import aiohttp
        while len(caller_got) < 10 * audio.FRAME_BYTES:
            msg = await asyncio.wait_for(ws.receive(), 5)
            if msg.type == aiohttp.WSMsgType.BINARY:
                caller_got.extend(msg.data)

    await asyncio.gather(send_caller(), recv_caller())

    # phone->caller direction arrived at the caller WS
    assert len(caller_got) >= 10 * audio.FRAME_BYTES
    await client.post("/admin/hangup?token=dev-token")
    await asyncio.sleep(0.2)

    # recordings exist and phone leg audio was recorded
    rec = tmp_path / "recordings" / call_id
    assert (rec / "caller.wav").exists()
    assert (rec / "phone.wav").exists() and (rec / "mix.wav").exists()
    phone_leg = audio.wav_read_frames(rec / "phone.wav")
    assert len(phone_leg) >= 10
    row = db._conn.execute(
        "SELECT outcome FROM calls WHERE call_id=?", (call_id,)).fetchone()
    assert row and row[0] == "completed"

    await ws.close(); await client.close()
    await ctl.stop(); await bus.stop(); db.close()
```

Note on `send_frame`: the caller→phone direction is asserted via `phone_got` only if the
controller wires the pump into the AudioSocket session. Add exactly that to
`Controller._on_audiosocket_session` (it already receives the session): after wiring
`on_audio`/`on_closed`, if the phone leg has `set_audio_sender`, call
`phone_leg.set_audio_sender(sess.send_audio)`; **also** make `Controller` keep
`self._call_phone_leg` when building the call so it can do this. Then extend the test's
`FakeAtaPhoneLeg` with `set_audio_sender(cb)` storing the callback and forwarding
`send_frame` to it, and assert `await asyncio.wait_for(fake_done.wait(), 5)` plus
`len(phone_got) >= 15` before hangup. (This is the same seam `AriPhoneLeg` uses — one
mechanism, two implementations.)

- [ ] **Step 2: Run the test — fix wiring until green**

Run: `cd hotline/server; python -m pytest tests/test_e2e_wav.py -q`
Expected first run: FAIL on the `phone_got`/`set_audio_sender` wiring → implement the
controller change described in the note (≈6 lines in `controller.py`) → re-run.
Expected: `1 passed`, plus the full suite: `python -m pytest -q` all green.

- [ ] **Step 3: Commit**

```bash
git add hotline/server/tests/test_e2e_wav.py hotline/server/hotline/controller.py
git commit -m "test(hotline): end-to-end WAV pipeline through WS + AudioSocket"
```

---

### Task 14: Asterisk config templates + deploy files

No hardware needed — these are reviewed files; they're exercised in Task 15.

**Files:**
- Create: `hotline/server/asterisk/pjsip.conf.tmpl`, `hotline/server/asterisk/extensions.conf.tmpl`, `hotline/server/asterisk/http.conf.tmpl`, `hotline/server/asterisk/ari.conf.tmpl`, `hotline/server/asterisk/README.md`
- Create: `hotline/server/deploy/hotline.service`, `hotline/server/deploy/install.sh`, `hotline/server/deploy/tunnel-ingress.md`

- [ ] **Step 1: Write Asterisk templates**

`hotline/server/asterisk/pjsip.conf.tmpl`:
```ini
; Pork Phone — ATA endpoint. LAN-only; ACL pins the ATA's IP (spec §4.3).
; Replace __ATA_IP__, __SIP_PASSWORD__, __PI_LAN_IP__ at install.

[transport-udp]
type=transport
protocol=udp
bind=__PI_LAN_IP__:5060      ; LAN interface only — never the tunnel

[ata]
type=endpoint
context=pork
disallow=all
allow=ulaw,alaw,slin
auth=ata-auth
aors=ata
callerid=PORK PHONE <2000>
dtmf_mode=rfc4733
; hook-flash handling: none (rotary; spec §5.1)

[ata-auth]
type=auth
auth_type=userpass
username=ata
password=__SIP_PASSWORD__

[ata]
type=aor
max_contacts=1
qualify_frequency=30

[ata-acl]
type=acl
deny=0.0.0.0/0.0.0.0
permit=__ATA_IP__/255.255.255.255
```

`hotline/server/asterisk/extensions.conf.tmpl`:
```ini
; Calls are ARI-originated into the 'pork' Stasis app; the dialplan is a
; fallback landing context only. AudioSocket dialplan fallback (if ARI
; externalMedia audiosocket is unavailable on this Asterisk version):
;   exten => 100,1,Answer()
;    same => n,AudioSocket(${PORK_UUID},127.0.0.1:__AUDIOSOCKET_PORT__)
[pork]
exten => _X.,1,NoOp(Pork Phone inbound ${EXTEN})
 same => n,Stasis(pork)
 same => n,Hangup()
```

`hotline/server/asterisk/http.conf.tmpl`:
```ini
[general]
enabled=yes
bindaddr=127.0.0.1           ; ARI is localhost-only (spec §4.3)
bindport=8088
```

`hotline/server/asterisk/ari.conf.tmpl`:
```ini
[general]
enabled=yes
pretty=no

[hotline]
type=user
read_only=no
password=__ARI_PASSWORD__
```

`hotline/server/asterisk/README.md`:
```markdown
# Asterisk config for the Pork Phone (Pi)

Install: `sudo apt install asterisk` (Asterisk 20 on Bookworm). Copy each
`*.tmpl` into /etc/asterisk/ (merge, don't clobber existing dialplan if any),
substituting __ATA_IP__, __PI_LAN_IP__, __SIP_PASSWORD__, __ARI_PASSWORD__,
__AUDIOSOCKET_PORT__ (default 9101). Then `sudo systemctl restart asterisk`.

Verify at install (Task 15):
1. `asterisk -rx "core show version"` — record it.
2. `asterisk -rx "module show like audiosocket"` — res_audiosocket +
   chan_audiosocket loaded.
3. ARI externalMedia audiosocket support: try the app's test-ring; if
   originate/externalMedia rejects `encapsulation=audiosocket`, fall back to
   the dialplan route in extensions.conf.tmpl (app originates to
   PJSIP/ata with context=pork, extension 100 — set PORK_UUID channel var,
   already passed by AriClient.originate_phone).
4. AudioSocket frame kinds against res_audiosocket docs (0x00/0x01/0x10).

MicroSIP stand-in for the ATA (Phase 1, no hardware): register MicroSIP on
Paul's PC as endpoint `ata` (username ata, the SIP password, server = Pi LAN
IP). To Asterisk it IS the ATA. Swap to the HT802V2 later by pointing the
real ATA at the same registrar with the same creds — zero config drift.
```

- [ ] **Step 2: Write deploy files**

`hotline/server/deploy/hotline.service`:
```ini
# Pork Phone hotline — systemd unit (spec §4.3 sandboxing)
[Unit]
Description=Pork Phone hotline service
After=network-online.target asterisk.service
Wants=network-online.target

[Service]
Type=simple
User=hotline
Group=hotline
WorkingDirectory=/opt/hotline/server
EnvironmentFile=/etc/hotline/hotline.env
ExecStart=/opt/hotline/venv/bin/python -m hotline
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/var/lib/hotline

[Install]
WantedBy=multi-user.target
```

`hotline/server/deploy/install.sh`:
```bash
#!/usr/bin/env bash
# Pork Phone hotline — Pi install. Run from repo root as a sudoer.
set -euo pipefail

sudo useradd --system --home /var/lib/hotline --create-home hotline 2>/dev/null || true
sudo mkdir -p /opt/hotline /etc/hotline /var/lib/hotline
sudo rsync -a --delete hotline/server/ /opt/hotline/server/
sudo python3 -m venv /opt/hotline/venv
sudo /opt/hotline/venv/bin/pip install -r /opt/hotline/server/requirements.txt

if [ ! -f /etc/hotline/hotline.env ]; then
  sudo tee /etc/hotline/hotline.env >/dev/null <<'EOF'
HOTLINE_ENV=prod
HOTLINE_DATA_DIR=/var/lib/hotline
HOTLINE_ADMIN_TOKEN=CHANGE-ME
HOTLINE_ARI_PASSWORD=CHANGE-ME
HOTLINE_DELAY_N=4
EOF
  sudo chmod 600 /etc/hotline/hotline.env
  echo ">>> edit /etc/hotline/hotline.env (tokens) before starting"
fi
sudo chown -R hotline:hotline /var/lib/hotline
sudo cp hotline/server/deploy/hotline.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable hotline
echo "install done — start with: sudo systemctl start hotline"
```

`hotline/server/deploy/tunnel-ingress.md`:
```markdown
# Cloudflare tunnel ingress for the hotline

Add to the existing tunnel config on the Pi (BEFORE the catch-all rule),
then restart cloudflared. Subdomain decision per spec §10.

    - hostname: phone.thekartoff.com
      service: http://127.0.0.1:9100

Plus a DNS route: `cloudflared tunnel route dns <tunnel> phone.thekartoff.com`
(or the dashboard equivalent). WebSockets pass through by default.
Verify after: `curl https://phone.thekartoff.com/healthz` → {"ok": true}
from OUTSIDE the LAN (phone hotspot).
```

- [ ] **Step 3: Sanity-check the shell script**

Run: `bash -n hotline/server/deploy/install.sh`
Expected: no output (parses clean).

- [ ] **Step 4: Commit**

```bash
git add hotline/server/asterisk hotline/server/deploy
git commit -m "feat(hotline): asterisk templates, systemd unit, install script, tunnel ingress doc"
```

---

### Task 15: Pi deployment + MicroSIP first internet ring (Paul-in-the-loop)

The plan's finish line, still **zero purchased hardware**: MicroSIP on Paul's PC plays the
ATA's role. Requires Paul (Pi access, Cloudflare dashboard, a phone hotspot for the
outside-the-LAN check). Run as a guided session, not a subagent task.

**Files:**
- Modify (on the Pi, not the repo): `/etc/asterisk/*.conf`, `/etc/hotline/hotline.env`, tunnel config.
- Modify: `hotline/server/asterisk/README.md` — record the verification results (Asterisk version, externalMedia verdict).

**Checklist:**

- [ ] **Step 1: Deploy the service.** On the Pi: pull the repo, `bash hotline/server/deploy/install.sh`, set real tokens in `/etc/hotline/hotline.env` (generate: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`), `sudo systemctl start hotline`, `curl http://127.0.0.1:9100/healthz` → `{"ok": true}`.
- [ ] **Step 2: Install + configure Asterisk** per `asterisk/README.md` (substitute the MicroSIP machine's IP as `__ATA_IP__` for now). Restart, then run the README's verification commands; **record Asterisk version + externalMedia-audiosocket verdict in the README and commit**. If externalMedia rejects audiosocket, switch `AriPhoneLeg._on_answered` to the documented dialplan fallback (originate with `context="pork", extension="100"` instead of `app=`; `POST /ari/channels` accepts context/extension/priority — one-line change, noted in README).
- [ ] **Step 3: Register MicroSIP** as `ata` against the Pi (creds from pjsip.conf). Asterisk shows the registration: `asterisk -rx "pjsip show contacts"`.
- [ ] **Step 4: LAN ring.** On the Pi: `HOTLINE_ECHO` must be unset/0 in the env. From a browser on the LAN: `http://<pi>:9100` is NOT reachable (localhost bind — expected); instead SSH-tunnel for this pre-tunnel check: `ssh -L 9100:127.0.0.1:9100 pi@<pi>` then `http://127.0.0.1:9100/test` → CONNECT MIC → RING PHONE → **MicroSIP rings** → answer in MicroSIP → talk both ways. Events log shows ringing/active; hang up; `recordings/<id>/mix.wav` exists on the Pi and plays back the conversation.
- [ ] **Step 5: Tunnel ingress** per `deploy/tunnel-ingress.md`. From a phone-hotspot laptop (outside the LAN): `https://phone.thekartoff.com/test` → full ring → MicroSIP → two-way audio. Note round-trip feel; if audio stutters, capture `journalctl -u hotline` and note it as the Phase-1 jitter verdict (§11 ladder decision point — the spec's WS→TURN→VPS ladder).
- [ ] **Step 6: Record outcomes.** Update `asterisk/README.md` (versions, verdicts, any fallback used) and the spec's §15 known-unverified list (tick WS-over-tunnel + AudioSocket items). Commit:

```bash
git add hotline/server/asterisk/README.md docs/superpowers/specs/2026-07-12-pork-phone-hotline-design.md
git commit -m "docs(hotline): record Phase-1 first-ring verdicts (Asterisk version, WS-over-tunnel)"
```

**Done when:** a browser outside the house rings a softphone standing in for the ATA,
two-way audio flows, a recording lands on disk, and the events feed narrated it — i.e.
the day the HT802V2 + adaptor arrive, "first real ring" is spec §14 Phase 0/1 hardware
config only, zero new code.

---

## Self-Review (performed at write time)

1. **Spec coverage (Plan-1 scope):** §6 call path — Tasks 4, 8, 9, 11, 12; §4.3 app
   hardening — Task 14 unit + localhost binds (Tasks 4, 11, 12); §7.3 feeds — Task 6;
   §11 fail-closed rows (boot closed, unknown UUID, disk guard, caller-lost grace) —
   Tasks 4, 8, 11; §12 schema + retention — Tasks 5, 7; §14 Phase 1 — Task 15. Twitch
   (§9), page (§10 caller page), daemon (§7) are Plans 2–3 by scope statement.
2. **Placeholder scan:** the only intentional deferred items are marked as verification
   steps with named fallbacks (ARI externalMedia params, AudioSocket kind bytes) — both
   carry exact fallback instructions, not TBDs.
3. **Type consistency:** `PhoneLeg` protocol (ring/hangup/send_frame) matches FakePhone
   (Task 8), EchoPhoneLeg (Task 11), AriPhoneLeg (Task 12), FakeAtaPhoneLeg (Task 13);
   `set_audio_sender` seam is defined in Task 12 and consumed in Task 13's controller
   note; event type strings match between Tasks 6, 8, and 11's tests; frame constants
   used everywhere come from `audio.py` only.
