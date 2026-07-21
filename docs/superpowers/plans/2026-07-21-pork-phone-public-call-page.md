# Pork Phone Public Call Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the public call page at the root of `phone.thekartoff.com`: a line-lease
state machine so one caller at a time can ring the 802 with fair recovery, a public
read-only events feed, phone SFX, audio-device settings, and ATA-unplugged detection.

**Architecture:** All server work lives in the existing `hotline/server/hotline/` aiohttp
service (zero imports from `pi/`). A new `lease.py` guards the single caller slot;
`controller.py` composes lease + ATA reachability into `line_state` events on the existing
`EventBus`; `http.py` grows lease-gated public endpoints and Origin checks. The page itself
is hand-written static HTML/CSS/JS in `hotline/server/hotline/static/` (no build step),
reusing the existing capture/playback worklets.

**Tech Stack:** Python 3.13+ asyncio + aiohttp (>=3.14), pytest (asyncio auto mode),
vanilla JS + WebAudio, MicroSIP wav SFX.

**Spec:** `docs/superpowers/specs/2026-07-21-pork-phone-public-call-page-design.md` —
read it first. Locked visuals: `docs/design/pork-phone-call-page/states-locked.html`
(state wording/colours) and `layout-reference.html` (layout, option A "plain").

## Global Constraints

- Page copy: all lowercase captions/statuses; **never use em dashes** anywhere in page copy.
- State wording exactly as the spec §2.1 table (e.g. pill `idle`, caption `press to call`).
- Colours: ground `#0b0c0e`, ink `#101114`, ink-2 `#191a1d`, paper `#f3f4f6`, muted
  `#9a9ca1`, dim `#6b6d73`, rule `#26272c`, button green `#16a34a`, button red `#dc2626`,
  dot green `#22c55e`. Font Inter 700/800 via system fallback stack (the hotline serves no
  webfonts; use `font-family:Inter,'Segoe UI',system-ui,sans-serif` — Inter renders where
  installed, Segoe UI otherwise; do NOT copy woff2 files into the hotline).
- Timeouts (config defaults): claim window 10 s · WS reconnect grace 15 s · ring timeout
  30 s · call backstop 1800 s · ATA poll 15 s.
- The internal lease machinery never appears in page copy.
- Run tests from `hotline/server/`: `python -m pytest` (pytest.ini sets asyncio auto).
  The full suite must stay green after every task.
- Windows dev shell; use forward-slash paths in bash commands.
- Commit after every task (at minimum); `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Config knobs (origins + timeouts)

**Files:**
- Modify: `hotline/server/hotline/config.py`
- Test: `hotline/server/tests/test_config.py` (append)

**Interfaces:**
- Produces: `Config.allowed_origins: tuple[str, ...]`, `Config.claim_window_s: float`,
  `Config.ws_grace_s: float`, `Config.ring_timeout_s: int`, `Config.call_backstop_s: int`,
  `Config.ata_poll_s: float` — later tasks read these off `cfg`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_config.py`)

```python
def test_new_knobs_defaults():
    cfg = Config.from_env({"HOTLINE_DATA_DIR": "/tmp/x"})
    assert cfg.claim_window_s == 10.0
    assert cfg.ws_grace_s == 15.0
    assert cfg.ring_timeout_s == 30
    assert cfg.call_backstop_s == 1800
    assert cfg.ata_poll_s == 15.0
    # dev origins derived from the http port; prod origin always present
    assert "https://phone.thekartoff.com" in cfg.allowed_origins
    assert "http://127.0.0.1:9100" in cfg.allowed_origins
    assert "http://localhost:9100" in cfg.allowed_origins


def test_allowed_origins_env_override():
    cfg = Config.from_env({"HOTLINE_DATA_DIR": "/tmp/x",
                           "HOTLINE_ALLOWED_ORIGINS": "https://a.example, https://b.example"})
    assert "https://a.example" in cfg.allowed_origins
    assert "https://b.example" in cfg.allowed_origins
    # localhost dev origins are still appended so echo-mode dev keeps working
    assert "http://127.0.0.1:9100" in cfg.allowed_origins
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd hotline/server && python -m pytest tests/test_config.py -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'claim_window_s'`

- [ ] **Step 3: Implement** — add fields to the dataclass and `from_env`:

```python
# new dataclass fields (after echo_mode)
    allowed_origins: tuple[str, ...]
    claim_window_s: float
    ws_grace_s: float
    ring_timeout_s: int
    call_backstop_s: int
    ata_poll_s: float
```

In `from_env`, before the `return cls(...)`:

```python
        http_port = int(environ.get("HOTLINE_HTTP_PORT", "9100"))
        origins = [o.strip() for o in
                   environ.get("HOTLINE_ALLOWED_ORIGINS",
                               "https://phone.thekartoff.com").split(",") if o.strip()]
        origins += [f"http://127.0.0.1:{http_port}", f"http://localhost:{http_port}"]
```

and in the `return cls(...)` call replace the inline `http_port=` computation with the
variable and add:

```python
            http_port=http_port,
            allowed_origins=tuple(origins),
            claim_window_s=float(environ.get("HOTLINE_CLAIM_WINDOW_S", "10")),
            ws_grace_s=float(environ.get("HOTLINE_WS_GRACE_S", "15")),
            ring_timeout_s=int(environ.get("HOTLINE_RING_TIMEOUT_S", "30")),
            call_backstop_s=int(environ.get("HOTLINE_CALL_BACKSTOP_S", "1800")),
            ata_poll_s=float(environ.get("HOTLINE_ATA_POLL_S", "15")),
```

- [ ] **Step 4: Run the whole suite**

Run: `cd hotline/server && python -m pytest -q`
Expected: all pass (existing tests construct Config only via `from_env`, so new
required fields with env defaults are safe).

- [ ] **Step 5: Commit**

```bash
git add hotline/server/hotline/config.py hotline/server/tests/test_config.py
git commit -m "feat(hotline): config knobs for lease timeouts + allowed origins"
```

---

### Task 2: LineLease state machine

**Files:**
- Create: `hotline/server/hotline/lease.py`
- Test: `hotline/server/tests/test_lease.py`

**Interfaces:**
- Consumes: nothing (pure asyncio + stdlib).
- Produces: `LineLease(publish, claim_window_s, backstop_s)` with:
  `claim() -> str` (raises `LineBusy`), `valid(lease_id) -> bool`,
  `mark_ringing(lease_id)`, `mark_oncall(lease_id)` (both raise `KeyError` on a stale id),
  `release(lease_id=None)` (None forces), `on_expired(cb: Callable[[str], None])`,
  `state: str` (one of `lease.IDLE/HELD/RINGING/ONCALL`), `since: float`,
  `snapshot() -> dict` (the `line_state` event body **without** composition — Task 3 wraps it).
  `publish` is called with the snapshot dict on every state change.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_lease.py
from __future__ import annotations

import asyncio

import pytest

from hotline import lease as lease_mod
from hotline.lease import LineBusy, LineLease


def make(events, **kw):
    kw.setdefault("claim_window_s", 0.15)
    kw.setdefault("backstop_s", 10.0)
    return LineLease(events.append, **kw)


async def test_claim_transitions_and_publishes():
    events: list[dict] = []
    ll = make(events)
    assert ll.state == lease_mod.IDLE
    lid = ll.claim()
    assert ll.state == lease_mod.HELD and ll.valid(lid)
    with pytest.raises(LineBusy):
        ll.claim()
    ll.mark_ringing(lid)
    ll.mark_oncall(lid)
    ll.release(lid)
    assert ll.state == lease_mod.IDLE and not ll.valid(lid)
    assert [e["state"] for e in events] == ["held", "ringing", "oncall", "idle"]
    assert all(e["type"] == "line_state" and "since" in e for e in events)
    assert all("lease" not in e for e in events)  # credential never broadcast


async def test_stale_id_rejected():
    ll = make([])
    lid = ll.claim()
    ll.release(lid)
    with pytest.raises(KeyError):
        ll.mark_ringing(lid)
    ll.release(lid)  # releasing a stale id is a no-op, not an error


async def test_claim_window_expires_held_lease():
    expired: list[str] = []
    ll = make([], claim_window_s=0.05)
    ll.on_expired(expired.append)
    lid = ll.claim()
    await asyncio.sleep(0.15)
    assert expired == [lid]        # callback fired
    # callback owner is responsible for release; simulate it:
    ll.release(lid)
    assert ll.state == lease_mod.IDLE


async def test_ringing_cancels_claim_window():
    expired: list[str] = []
    ll = make([], claim_window_s=0.05)
    ll.on_expired(expired.append)
    lid = ll.claim()
    ll.mark_ringing(lid)
    await asyncio.sleep(0.15)
    assert expired == []           # window no longer applies
    ll.release(lid)


async def test_backstop_fires_even_oncall():
    expired: list[str] = []
    ll = make([], claim_window_s=5.0, backstop_s=0.1)
    ll.on_expired(expired.append)
    lid = ll.claim()
    ll.mark_ringing(lid)
    ll.mark_oncall(lid)
    await asyncio.sleep(0.2)
    assert expired == [lid]


async def test_release_cancels_timers():
    expired: list[str] = []
    ll = make([], claim_window_s=0.05, backstop_s=0.05)
    ll.on_expired(expired.append)
    lid = ll.claim()
    ll.release(lid)
    await asyncio.sleep(0.15)
    assert expired == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd hotline/server && python -m pytest tests/test_lease.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hotline.lease'`

- [ ] **Step 3: Implement `hotline/server/hotline/lease.py`**

```python
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
```

- [ ] **Step 4: Run tests**

Run: `cd hotline/server && python -m pytest tests/test_lease.py -v`
Expected: all 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add hotline/server/hotline/lease.py hotline/server/tests/test_lease.py
git commit -m "feat(hotline): LineLease single-slot state machine with claim window + backstop"
```

---

### Task 3: Controller integration (lease + line_state + reachability)

**Files:**
- Modify: `hotline/server/hotline/controller.py`
- Modify: `hotline/server/hotline/call.py` (one optional callback param)
- Test: `hotline/server/tests/test_controller_lease.py` (new)

**Interfaces:**
- Consumes: `LineLease` from Task 2, `Config` knobs from Task 1.
- Produces (used by Task 4's HTTP layer):
  - `Controller.claim_line() -> str` — raises `LineBusy` (busy) or `PhoneUnplugged`.
  - `Controller.ring_with_lease(lease_id: str) -> str` — returns call_id; raises
    `KeyError` (stale lease) / `RuntimeError` (no caller WS, low disk, wrong state).
  - `Controller.hangup_with_lease(lease_id: str) -> bool` — raises `KeyError` on stale.
  - `Controller.attach_caller_ws(send, lease_id: str | None = None)` — lease validated
    when given; still raises `RuntimeError` when the slot is occupied.
  - `Controller.line_snapshot() -> dict` — `line_state` event composed with reachability
    (`idle` becomes `unplugged` when the ATA is unreachable).
  - `Controller.set_phone_reachable(ok: bool)` — Task 5's poll loop calls this.
  - `PhoneUnplugged` exception class exported from `controller.py`.
- `CallSession.__init__` gains `on_answered: Optional[Callable[[], None]] = None`,
  invoked once inside `on_phone_answered()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_controller_lease.py
from __future__ import annotations

import asyncio

import pytest

from hotline.config import Config
from hotline.controller import Controller, PhoneUnplugged
from hotline.db import Db
from hotline.events import EventBus
from hotline.lease import LineBusy


async def make_ctl(tmp_path, unused_tcp_port, **env):
    e = {"HOTLINE_ENV": "dev", "HOTLINE_DATA_DIR": str(tmp_path),
         "HOTLINE_AUDIOSOCKET_PORT": str(unused_tcp_port), "HOTLINE_ECHO": "1"}
    e.update(env)
    cfg = Config.from_env(e)
    bus = EventBus(delay_n=0.05)
    await bus.start()
    db = Db(tmp_path / "h.db"); db.init()
    ctl = Controller(cfg, bus, db, phone_leg_factory=None)
    await ctl.start()
    return cfg, bus, db, ctl


async def teardown(bus, db, ctl):
    await ctl.stop(); await bus.stop(); db.close()


async def attach(ctl, lease_id):
    sent = []
    async def send(frame): sent.append(frame)
    await ctl.attach_caller_ws(send, lease_id=lease_id)
    return sent


async def test_claim_ring_answer_hangup_flow(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_ctl(tmp_path, unused_tcp_port)
    q = bus.subscribe("rt")
    lid = ctl.claim_line()
    with pytest.raises(LineBusy):
        ctl.claim_line()
    await attach(ctl, lid)
    call_id = await ctl.ring_with_lease(lid)
    assert call_id
    # echo leg answers instantly -> lease should be oncall
    assert ctl.lease.state == "oncall"
    assert await ctl.hangup_with_lease(lid) is True
    await asyncio.sleep(0.1)  # reap
    assert ctl.lease.state == "idle"
    states = [e["state"] for e in _drain(q) if e.get("type") == "line_state"]
    assert states[-1] == "idle"
    assert "oncall" in states and "held" in states
    await teardown(bus, db, ctl)


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


async def test_ring_requires_matching_lease(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_ctl(tmp_path, unused_tcp_port)
    with pytest.raises(KeyError):
        await ctl.ring_with_lease("not-a-lease")
    await teardown(bus, db, ctl)


async def test_claim_window_frees_the_line(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_ctl(tmp_path, unused_tcp_port,
                                     HOTLINE_CLAIM_WINDOW_S="0.05")
    ctl.claim_line()
    await asyncio.sleep(0.2)
    assert ctl.lease.state == "idle"     # expired and auto-released
    ctl.claim_line()                     # line reusable
    await teardown(bus, db, ctl)


async def test_backstop_ends_live_call(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_ctl(tmp_path, unused_tcp_port,
                                     HOTLINE_CALL_BACKSTOP_S="1")
    lid = ctl.claim_line()
    await attach(ctl, lid)
    call_id = await ctl.ring_with_lease(lid)
    await asyncio.sleep(1.5)
    assert ctl.lease.state == "idle"
    row = db._conn.execute("SELECT outcome FROM calls WHERE call_id=?",
                           (call_id,)).fetchone()
    assert row and row[0] in ("dropped", "completed")
    await teardown(bus, db, ctl)


async def test_unplugged_refuses_claims_and_snapshots(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_ctl(tmp_path, unused_tcp_port)
    ctl.set_phone_reachable(False)
    assert ctl.line_snapshot()["state"] == "unplugged"
    with pytest.raises(PhoneUnplugged):
        ctl.claim_line()
    ctl.set_phone_reachable(True)
    assert ctl.line_snapshot()["state"] == "idle"
    ctl.claim_line()
    await teardown(bus, db, ctl)


async def test_ws_drop_grace_releases_lease(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_ctl(tmp_path, unused_tcp_port,
                                     HOTLINE_WS_GRACE_S="0.05")
    lid = ctl.claim_line()
    await attach(ctl, lid)
    await ctl.ring_with_lease(lid)
    ctl.detach_caller_ws()               # tab died
    await asyncio.sleep(0.5)             # grace 0.05 + teardown
    assert ctl.lease.state == "idle"
    await teardown(bus, db, ctl)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd hotline/server && python -m pytest tests/test_controller_lease.py -v`
Expected: FAIL — `ImportError: cannot import name 'PhoneUnplugged'`

- [ ] **Step 3: Implement**

`call.py` — add the answer hook (three small edits):

```python
# __init__ signature gains (after grace_s):
                 grace_s: float = 10.0,
                 on_answered: Optional[Callable[[], None]] = None) -> None:
# ... store it:
        self._on_answered = on_answered
# in on_phone_answered(), after self._answered_at = time.monotonic():
        if self._on_answered:
            self._on_answered()
```

`controller.py` — new exception + lease wiring. Add imports:

```python
from .lease import HELD, ONCALL, RINGING, LineBusy, LineLease
```

(`LineBusy` is imported so `http.py` can re-use it via `controller`;
`RuntimeError` stays the "wrong state" error for bench parity.)

```python
class PhoneUnplugged(Exception):
    pass
```

In `Controller.__init__` (after `self._reap_task = None`):

```python
        self.lease = LineLease(self._publish_line_state,
                               cfg.claim_window_s, cfg.call_backstop_s)
        self.lease.on_expired(self._on_lease_expired)
        self._phone_reachable = True   # echo mode never flips this; real mode
                                       # is driven by the ARI poll (Task 5)
        self._call_lease_id: Optional[str] = None
```

New methods (place after `on_caller_audio`):

```python
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
```

`test_ring` changes — signature and the `CallSession` construction:

```python
    async def test_ring(self, seconds: float = 60, caller_label: str = "test",
                        lease_id: Optional[str] = None) -> str:
```

`create_call` gets `caller_label` instead of the literal `"test"`; the
`CallSession(...)` call gains:

```python
                           grace_s=self.cfg.ws_grace_s,
                           on_answered=(
                               (lambda: self.lease.mark_oncall(lease_id))
                               if lease_id else None),
```

and `caller_label=caller_label` replaces `caller_label="test"`.

`_reap` — after the `self._call_phone_leg = None` line inside the `if`:

```python
            if self._call_lease_id is not None:
                self.lease.release(self._call_lease_id)
                self._call_lease_id = None
```

`start()` — after the legacy `lines_state` publish, add a boot snapshot:

```python
        self.bus.publish(self.line_snapshot())
```

Note on `mark_oncall` via `on_answered`: it can raise `KeyError` if the lease was
force-released in the same tick; wrap it:

```python
                           on_answered=(
                               (lambda: self._safe_mark_oncall(lease_id))
                               if lease_id else None),
```

with:

```python
    def _safe_mark_oncall(self, lease_id: str) -> None:
        try:
            self.lease.mark_oncall(lease_id)
        except KeyError:
            pass   # lease died between answer and mark; reap will clean up
```

- [ ] **Step 4: Run the whole suite**

Run: `cd hotline/server && python -m pytest -q`
Expected: all pass, including the 7 new tests and the untouched bench-path tests
(`test_controller_http.py` uses token calls with no lease; `lease_id=None` keeps that
path identical).

- [ ] **Step 5: Commit**

```bash
git add hotline/server/hotline/controller.py hotline/server/hotline/call.py hotline/server/tests/test_controller_lease.py
git commit -m "feat(hotline): controller line lease, line_state composition, reachability flag"
```

---

### Task 4: HTTP surface (public endpoints, Origin checks, lease-auth WS, page route)

**Files:**
- Modify: `hotline/server/hotline/http.py`
- Test: `hotline/server/tests/test_public_api.py` (new)

**Interfaces:**
- Consumes: `Controller.claim_line/ring_with_lease/hangup_with_lease/line_snapshot/
  attach_caller_ws(send, lease_id=...)`, `PhoneUnplugged`, `LineBusy` (Task 3),
  `cfg.allowed_origins` (Task 1).
- Produces (used by the page in Task 6):
  - `POST /call/claim` → `200 {"lease_id"}` | `409 {"error":"busy"|"unplugged"}` | `403`.
  - `POST /call/ring?lease=<id>` → `200 {"call_id"}` | `404 {"error":"stale_lease"}` |
    `409 {"error": <reason>}` | `403`.
  - `POST /call/hangup?lease=<id>` → `200 {"hungup":bool}` | `404` | `403`.
  - `GET /` → `static/index.html`.
  - `GET /ws/audio?lease=<id>` (or `?token=` for the bench) — 4009 close when slot busy,
    401 when neither credential is valid.
  - `GET /ws/events?feed=rt` — public, Origin-checked, first message is
    `controller.line_snapshot()`; `feed=delayed` still needs the token.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_public_api.py
from __future__ import annotations

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from hotline.config import Config
from hotline.controller import Controller
from hotline.db import Db
from hotline.events import EventBus
from hotline.http import make_app


async def make_stack(tmp_path, unused_tcp_port, **env):
    e = {"HOTLINE_ENV": "dev", "HOTLINE_DATA_DIR": str(tmp_path),
         "HOTLINE_AUDIOSOCKET_PORT": str(unused_tcp_port), "HOTLINE_ECHO": "1"}
    e.update(env)
    cfg = Config.from_env(e)
    bus = EventBus(delay_n=0.05)
    await bus.start()
    db = Db(tmp_path / "h.db"); db.init()
    ctl = Controller(cfg, bus, db, phone_leg_factory=None)
    await ctl.start()
    client = TestClient(TestServer(make_app(cfg, ctl)))
    await client.start_server()
    return cfg, bus, db, ctl, client


async def close_stack(client, ctl, bus, db):
    await client.close(); await ctl.stop(); await bus.stop(); db.close()


async def test_page_served_at_root(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    resp = await client.get("/")
    assert resp.status == 200
    assert "text/html" in resp.headers["Content-Type"]
    await close_stack(client, ctl, bus, db)


async def test_full_public_flow(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    ev = await client.ws_connect("/ws/events?feed=rt")       # no token
    hello = await asyncio.wait_for(ev.receive_json(), 5)
    assert hello["type"] == "line_state" and hello["state"] == "idle"

    lease = (await (await client.post("/call/claim")).json())["lease_id"]
    r2 = await client.post("/call/claim")
    assert r2.status == 409 and (await r2.json())["error"] == "busy"

    ws = await client.ws_connect(f"/ws/audio?lease={lease}")
    resp = await client.post(f"/call/ring?lease={lease}")
    assert resp.status == 200
    assert "call_id" in await resp.json()

    # events feed narrates the lease lifecycle to everyone
    states = set()
    while "oncall" not in states:
        msg = await asyncio.wait_for(ev.receive_json(), 5)
        if msg.get("type") == "line_state":
            states.add(msg["state"])
    assert {"held", "ringing", "oncall"} <= states

    resp = await client.post(f"/call/hangup?lease={lease}")
    assert resp.status == 200 and (await resp.json())["hungup"] is True
    await ws.close(); await ev.close()
    await close_stack(client, ctl, bus, db)


async def test_stale_lease_404s(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    assert (await client.post("/call/ring?lease=deadbeef")).status == 404
    assert (await client.post("/call/hangup?lease=deadbeef")).status == 404
    await close_stack(client, ctl, bus, db)


async def test_audio_ws_rejects_without_credential(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    resp = await client.get("/ws/audio")            # no lease, no token
    assert resp.status == 401
    await close_stack(client, ctl, bus, db)


async def test_bad_origin_rejected_good_origin_allowed(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    bad = {"Origin": "https://evil.example"}
    assert (await client.post("/call/claim", headers=bad)).status == 403
    resp = await client.get("/ws/events?feed=rt", headers=bad)
    assert resp.status == 403
    good = {"Origin": "https://phone.thekartoff.com"}
    assert (await client.post("/call/claim", headers=good)).status == 200
    await close_stack(client, ctl, bus, db)


async def test_delayed_feed_still_needs_token(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    assert (await client.get("/ws/events?feed=delayed")).status == 401
    ws = await client.ws_connect("/ws/events?feed=delayed&token=dev-token")
    await ws.close()
    await close_stack(client, ctl, bus, db)


async def test_unplugged_claim_409s(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    ctl.set_phone_reachable(False)
    resp = await client.post("/call/claim")
    assert resp.status == 409 and (await resp.json())["error"] == "unplugged"
    await close_stack(client, ctl, bus, db)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd hotline/server && python -m pytest tests/test_public_api.py -v`
Expected: FAIL — `/` 404s, `/call/claim` 404s, events WS 401s.

- [ ] **Step 3: Implement in `http.py`**

Imports gain:

```python
from .controller import PhoneUnplugged
from .lease import LineBusy
```

Helpers (after `_authed`):

```python
def _origin_ok(request: web.Request) -> bool:
    origin = request.headers.get("Origin")
    if origin is None:
        return True   # non-browser clients; the URL is the gate this phase
    return origin in request.app[CFG_KEY].allowed_origins
```

Handlers:

```python
async def _index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def _call_claim(request: web.Request) -> web.Response:
    if not _origin_ok(request):
        return web.json_response({"error": "forbidden"}, status=403)
    controller = request.app[CONTROLLER_KEY]
    try:
        lease_id = controller.claim_line()
    except LineBusy:
        return web.json_response({"error": "busy"}, status=409)
    except PhoneUnplugged:
        return web.json_response({"error": "unplugged"}, status=409)
    return web.json_response({"lease_id": lease_id})


async def _call_ring(request: web.Request) -> web.Response:
    if not _origin_ok(request):
        return web.json_response({"error": "forbidden"}, status=403)
    controller = request.app[CONTROLLER_KEY]
    lease = request.query.get("lease", "")
    try:
        call_id = await controller.ring_with_lease(lease)
    except KeyError:
        return web.json_response({"error": "stale_lease"}, status=404)
    except RuntimeError as e:
        return web.json_response({"error": str(e)}, status=409)
    return web.json_response({"call_id": call_id})


async def _call_hangup(request: web.Request) -> web.Response:
    if not _origin_ok(request):
        return web.json_response({"error": "forbidden"}, status=403)
    controller = request.app[CONTROLLER_KEY]
    lease = request.query.get("lease", "")
    try:
        hungup = await controller.hangup_with_lease(lease)
    except KeyError:
        return web.json_response({"error": "stale_lease"}, status=404)
    return web.json_response({"hungup": hungup})
```

`_ws_audio` — replace the `_authed` gate:

```python
async def _ws_audio(request: web.Request) -> web.WebSocketResponse:
    if not _origin_ok(request):
        raise web.HTTPForbidden()
    controller = request.app[CONTROLLER_KEY]
    lease = request.query.get("lease", "")
    lease_ok = lease and controller.lease.valid(lease)
    if not lease_ok and not _authed(request):
        raise web.HTTPUnauthorized()
    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(request)
    # ... rest identical (attach_caller_ws gains lease_id)
    try:
        await controller.attach_caller_ws(send, lease_id=lease if lease_ok else None)
```

`_ws_events` — public rt feed with hello:

```python
async def _ws_events(request: web.Request) -> web.WebSocketResponse:
    if not _origin_ok(request):
        raise web.HTTPForbidden()
    feed = request.query.get("feed", "rt")
    if feed not in ("rt", "delayed"):
        raise web.HTTPBadRequest()
    if feed == "delayed" and not _authed(request):
        raise web.HTTPUnauthorized()
    bus = request.app[BUS_KEY]
    controller = request.app[CONTROLLER_KEY]
    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(request)
    q = bus.subscribe(feed)
    try:
        await ws.send_json(controller.line_snapshot())   # hello
    except ConnectionError:
        bus.unsubscribe(feed, q)
        return ws
    # ... existing select loop unchanged
```

Routes in `make_app`:

```python
    app.router.add_get("/", _index)
    app.router.add_post("/call/claim", _call_claim)
    app.router.add_post("/call/ring", _call_ring)
    app.router.add_post("/call/hangup", _call_hangup)
```

Note: Task 6 creates `static/index.html`; until then `_index` 404s at runtime but the
test only needs the file to exist — so this task also creates a **placeholder**
`hotline/server/hotline/static/index.html` containing `<!doctype html><title>pork phone</title>`
which Task 6 replaces.

- [ ] **Step 4: Run the whole suite**

Run: `cd hotline/server && python -m pytest -q`
Expected: all pass. `test_http.py` / `test_controller_http.py` bench paths (token WS,
`/admin/*`) are untouched and must stay green.

- [ ] **Step 5: Commit**

```bash
git add hotline/server/hotline/http.py hotline/server/hotline/static/index.html hotline/server/tests/test_public_api.py
git commit -m "feat(hotline): public lease endpoints, Origin checks, public rt events feed, page route"
```

---

### Task 5: ATA reachability (ARI endpoint poll) + 30s ring timeout

**Files:**
- Modify: `hotline/server/hotline/ari.py`
- Modify: `hotline/server/hotline/__main__.py`
- Test: `hotline/server/tests/test_ari.py` (append)

**Interfaces:**
- Consumes: `Controller.set_phone_reachable` (Task 3), `cfg.ata_poll_s`,
  `cfg.ring_timeout_s` (Task 1).
- Produces: `AriClient.endpoint_state(tech="PJSIP", resource="ata") -> str`
  (Asterisk endpoint state: `"online"`/`"offline"`/`"unknown"`);
  `AriClient.originate_phone(caller_id, uuid, timeout_s=30)`;
  `watch_ata(ari, controller, poll_s, stop)` coroutine in `__main__.py`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_ari.py`; `make_fake_ari`
  already exists at the top of that file — extend it with one route, then add two tests)

Add to `make_fake_ari`, next to the other handlers:

```python
    async def endpoint(request: web.Request):
        record["endpoint_asked"] = (request.match_info["tech"],
                                    request.match_info["res"])
        return web.json_response({"technology": "PJSIP", "resource": "ata",
                                  "state": record.get("endpoint_state", "online")})
```

and register it with the other routes:

```python
    app.router.add_get("/ari/endpoints/{tech}/{res}", endpoint)
```

New tests at the bottom of the file:

```python
async def test_endpoint_state():
    record: dict = {}
    server = TestServer(make_fake_ari(record))
    await server.start_server()
    client = AriClient(f"http://127.0.0.1:{server.port}", "hotline", "pw")
    await client.connect()
    assert await client.endpoint_state() == "online"
    assert record["endpoint_asked"] == ("PJSIP", "ata")
    record["endpoint_state"] = "offline"
    assert await client.endpoint_state() == "offline"
    await client.close()
    await server.close()


async def test_endpoint_state_unknown_on_error():
    # no /ari/endpoints route at all -> 404 -> "unknown", no raise
    app = web.Application()

    async def events_ws(request: web.Request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for _ in ws:
            pass
        return ws

    app.router.add_get("/ari/events", events_ws)
    server = TestServer(app)
    await server.start_server()
    client = AriClient(f"http://127.0.0.1:{server.port}", "u", "p")
    await client.connect()
    assert await client.endpoint_state() == "unknown"
    await client.close()
    await server.close()


async def test_originate_sends_timeout():
    record: dict = {}
    server = TestServer(make_fake_ari(record))
    await server.start_server()
    client = AriClient(f"http://127.0.0.1:{server.port}", "hotline", "pw")
    await client.connect()
    await client.originate_phone("web", "u-9", timeout_s=30)
    assert record["originate"]["timeout"] == 30
    await client.close()
    await server.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd hotline/server && python -m pytest tests/test_ari.py -v`
Expected: new tests FAIL — `AttributeError: endpoint_state` / missing `timeout` key.

- [ ] **Step 3: Implement**

`ari.py`:

```python
    async def endpoint_state(self, tech: str = "PJSIP",
                             resource: str = "ata") -> str:
        assert self._session is not None
        async with self._session.get(
                f"{self._base}/ari/endpoints/{tech}/{resource}") as resp:
            if resp.status != 200:
                return "unknown"
            data = await resp.json()
            return data.get("state", "unknown")
```

`originate_phone` gains the ring timeout (Asterisk `timeout` is in seconds):

```python
    async def originate_phone(self, caller_id: str, channel_var_uuid: str,
                              timeout_s: int = 30) -> str:
        data = await self._post("/ari/channels", {
            "endpoint": "PJSIP/ata", "app": self.app, "callerId": caller_id,
            "timeout": timeout_s, "appArgs": "phone",
            "variables": {"PORK_UUID": channel_var_uuid}})
        return data["id"]
```

`__main__.py` — `AriPhoneLeg.ring` passes it through (the leg already holds no cfg;
give it the value at construction):

```python
    def __init__(self, session: CallSession, ari: AriClient,
                 audiosocket_port: int, ring_timeout_s: int = 30) -> None:
        ...
        self._ring_timeout_s = ring_timeout_s
    # in ring():
            self._channel_id = await self._ari.originate_phone(
                caller_name, self._session.call_id, self._ring_timeout_s)
```

and the factory in `build_and_run`:

```python
        def factory(session: CallSession) -> AriPhoneLeg:
            return AriPhoneLeg(session, ari, cfg.audiosocket_port,
                               cfg.ring_timeout_s)
```

The watcher, also in `__main__.py`:

```python
async def watch_ata(ari: AriClient, controller: Controller,
                    poll_s: float, stop: asyncio.Event) -> None:
    """Poll the ATA endpoint; drive the line's unplugged state."""
    while not stop.is_set():
        try:
            state = await ari.endpoint_state()
        except Exception:
            state = "unknown"
        controller.set_phone_reachable(state == "online")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), poll_s)
```

Wire it in `build_and_run` (real mode only), after `controller.start()`:

```python
    watch_task: Optional[asyncio.Task] = None
    if ari is not None:
        controller.set_phone_reachable(False)   # unknown until first poll answers
        watch_task = asyncio.create_task(
            watch_ata(ari, controller, cfg.ata_poll_s, stop))
```

and in the shutdown block, before `await controller.stop()`:

```python
    if watch_task:
        watch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watch_task
```

- [ ] **Step 4: Run the whole suite**

Run: `cd hotline/server && python -m pytest -q`
Expected: all pass (`test_main.py` exercises `build_and_run` in echo mode where
`ari is None`, so the watcher never starts there).

- [ ] **Step 5: Commit**

```bash
git add hotline/server/hotline/ari.py hotline/server/hotline/__main__.py hotline/server/tests/test_ari.py
git commit -m "feat(hotline): ATA reachability watcher + explicit 30s originate timeout"
```

---

### Task 6: The page (index.html, phone.css, phone.js, SFX)

**Files:**
- Create: `hotline/server/hotline/static/index.html` (replaces Task 4 placeholder)
- Create: `hotline/server/hotline/static/phone.css`
- Create: `hotline/server/hotline/static/phone.js`
- Create: `hotline/server/hotline/static/sfx/{ringing,hangup,ringtone,msgin,msgout}.wav`
  (copied from `temp/phonesfx/`)
- Test: `hotline/server/tests/test_public_api.py` (append one asset test)

**Interfaces:**
- Consumes: every endpoint from Task 4; `static/capture-worklet.js` +
  `static/playback-worklet.js` (existing, unchanged); the SFX envelope facts from spec §3.
- Produces: the shipped page. No later task consumes it.

- [ ] **Step 1: Copy the SFX**

```bash
mkdir -p hotline/server/hotline/static/sfx
cp temp/phonesfx/ringing.wav temp/phonesfx/hangup.wav temp/phonesfx/ringtone.wav \
   temp/phonesfx/msgin.wav temp/phonesfx/msgout.wav hotline/server/hotline/static/sfx/
```

- [ ] **Step 2: Append the asset test**

```python
async def test_page_assets_served(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_stack(tmp_path, unused_tcp_port)
    for path in ("/", "/static/phone.js", "/static/phone.css",
                 "/static/sfx/ringing.wav", "/static/sfx/hangup.wav",
                 "/static/sfx/ringtone.wav"):
        resp = await client.get(path)
        assert resp.status == 200, path
    body = await (await client.get("/")).text()
    assert "pork phone" in body
    assert "—" not in body          # no em dashes in page copy, ever
    await close_stack(client, ctl, bus, db)
```

Run: `cd hotline/server && python -m pytest tests/test_public_api.py -v` — the new
test FAILS (phone.js missing) until Step 3 lands.

- [ ] **Step 3: Write `index.html`**

```html
<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>pork phone</title>
<link rel="stylesheet" href="/static/phone.css">
</head><body>
<main class="page">
  <header class="head">
    <span class="brand">pork phone</span>
    <span class="pill" id="pill" hidden><span class="dot" id="dot"></span><span id="pill-text"></span></span>
  </header>

  <section class="callwrap">
    <button class="callbtn" id="callbtn" aria-label="call">
      <svg id="callicon" viewBox="0 0 24 24"><path d="M6.6 10.8c1.4 2.8 3.8 5.1 6.6 6.6l2.2-2.2c.3-.3.7-.4 1-.2 1.1.4 2.3.6 3.6.6.6 0 1 .4 1 1V20c0 .6-.4 1-1 1C10.6 21 3 13.4 3 4c0-.6.4-1 1-1h3.5c.6 0 1 .4 1 1 0 1.2.2 2.4.6 3.6.1.3 0 .7-.2 1l-2.3 2.2z"/></svg>
    </button>
    <span class="sub" id="caption">press to call</span>
  </section>

  <section class="settings">
    <h2 class="ttl">Settings</h2>
    <div class="row">
      <svg class="ic" viewBox="0 0 24 24"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.9V21h2v-3.1A7 7 0 0 0 19 11h-2z"/></svg>
      <select id="mic-sel"></select>
      <button class="testbtn" id="mic-test">Test</button>
    </div>
    <div class="row meter-row"><div class="meter"><div class="lv" id="mic-level"></div></div></div>
    <div class="row">
      <svg class="ic" viewBox="0 0 24 24"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 8v8a4.5 4.5 0 0 0 2.5-4z"/></svg>
      <select id="spk-sel"></select>
      <button class="testbtn" id="spk-test">Test</button>
    </div>
    <div class="row vol-row">
      <input type="range" id="vol" min="0" max="100" value="80" aria-label="volume">
    </div>
  </section>
</main>
<script src="/static/phone.js"></script>
</body></html>
```

- [ ] **Step 4: Write `phone.css`** (tokens + layout from the locked mockups)

```css
:root{
  --ink:#101114; --ink-2:#191a1d; --paper:#f3f4f6; --ground:#0b0c0e;
  --mut:#9a9ca1; --dim:#6b6d73; --rule:#26272c;
  --green:#16a34a; --red:#dc2626; --dot-green:#22c55e;
}
*{box-sizing:border-box;margin:0}
body{background:var(--ground);color:var(--paper);
  font-family:Inter,'Segoe UI',system-ui,sans-serif;font-weight:700;font-size:13px;
  font-variant-numeric:tabular-nums;min-height:100vh;display:flex;justify-content:center}
.page{width:min(460px,92vw);padding:48px 0 64px;display:flex;flex-direction:column}
.head{display:flex;align-items:center;justify-content:space-between;margin-bottom:56px;min-height:17px}
.brand{font-weight:800;font-size:15px;letter-spacing:.01em}
.pill{display:flex;align-items:center;gap:7px;font-size:11.5px}
.pill[hidden]{display:none}
.dot{width:8px;height:8px;border-radius:50%;background:var(--dim)}
.dot.green{background:var(--dot-green)}
.dot.cadence{animation:cadence 3s linear infinite}
/* the real AU double-ring: 400ms ring, 200 gap, 400 ring, 2000 silence */
@keyframes cadence{
  0%,1.5%,3%,4.5%,6%,7.5%,9%,10.5%,12%,13.3%{transform:translate(0,0)}
  .75%,2.25%,3.75%,5.25%,6.75%,8.25%,9.75%,11.25%{transform:translate(1.5px,-1px)}
  20%,21.5%,23%,24.5%,26%,27.5%,29%,30.5%,32%,33.3%{transform:translate(0,0)}
  20.75%,22.25%,23.75%,25.25%,26.75%,28.25%,29.75%,31.25%{transform:translate(-1.5px,-1px)}
  33.4%,100%{transform:translate(0,0)}
}
.callwrap{display:flex;flex-direction:column;align-items:center;margin-bottom:56px}
.callbtn{width:96px;height:96px;border-radius:50%;border:0;cursor:pointer;
  display:flex;align-items:center;justify-content:center;background:var(--green);
  transition:filter .12s}
.callbtn:hover{filter:brightness(1.12)}
.callbtn:active{filter:brightness(.92)}
.callbtn svg{width:48px;height:48px;fill:var(--paper);transition:transform .15s}
.callbtn.red{background:var(--red)}
.callbtn.red svg{transform:rotate(135deg)}
.callbtn.off{background:var(--ink-2);cursor:default}
.callbtn.off:hover,.callbtn.off:active{filter:none}
.callbtn.off svg{fill:var(--mut)}
.sub{font-size:12.5px;color:var(--mut);margin-top:16px;min-height:16px;transition:opacity .3s}
.settings{display:flex;flex-direction:column;gap:12px}
.ttl{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim);
  font-weight:700;margin-bottom:2px}
.row{display:flex;align-items:center;gap:10px}
.ic{width:18px;height:18px;flex-shrink:0;fill:var(--mut)}
select{flex:1;appearance:none;background:var(--ink-2);border:1px solid var(--rule);
  color:var(--paper);font:inherit;font-size:12px;padding:8px 11px;cursor:pointer;
  border-radius:0;min-width:0}
select:hover,select:focus{border-color:var(--dim);outline:none}
.testbtn{background:var(--ink-2);border:1px solid var(--rule);color:var(--mut);
  font:inherit;font-size:11px;padding:8px 13px;cursor:pointer}
.testbtn:hover{color:var(--paper);border-color:var(--dim)}
.testbtn.on{color:var(--paper);border-color:var(--dim)}
.meter-row,.vol-row{padding-left:28px}
.meter{height:5px;background:var(--ink-2);border:1px solid var(--rule);flex:1;overflow:hidden}
.lv{height:100%;width:0;background:var(--dot-green);transition:width .06s linear}
input[type=range]{flex:1;accent-color:var(--paper);background:transparent}
@media (prefers-reduced-motion: reduce){.dot.cadence{animation:none}}
```

- [ ] **Step 5: Write `phone.js`**

```js
/* pork phone page. State is driven by the events WS (line_state) plus what this
   page knows it did (its own lease). All audio (call + sfx) rides one
   AudioContext through one gain node so the output device + volume apply to
   everything. */
(() => {
  const $ = (id) => document.getElementById(id);
  const btn = $("callbtn"), caption = $("caption"),
        pill = $("pill"), pillText = $("pill-text"), dot = $("dot"),
        micSel = $("mic-sel"), spkSel = $("spk-sel"),
        micTest = $("mic-test"), spkTest = $("spk-test"),
        vol = $("vol"), micLevel = $("mic-level");

  const store = {
    get input()  { return localStorage.getItem("pp.input") || ""; },
    set input(v) { localStorage.setItem("pp.input", v); },
    get output() { return localStorage.getItem("pp.output") || ""; },
    set output(v){ localStorage.setItem("pp.output", v); },
    get volume() { return +(localStorage.getItem("pp.volume") ?? 80); },
    set volume(v){ localStorage.setItem("pp.volume", String(v)); },
  };

  // ---- audio graph ---------------------------------------------------------
  let ctx = null, gain = null, playNode = null, fallbackEl = null;
  const sfxBuf = {};   // name -> AudioBuffer
  let ringLoop = null; // AudioBufferSourceNode while ringing

  async function ensureCtx() {
    if (ctx) { if (ctx.state === "suspended") await ctx.resume(); return; }
    ctx = new AudioContext({ sampleRate: 48000 });
    gain = new GainNode(ctx, { gain: store.volume / 100 });
    await ctx.audioWorklet.addModule("/static/capture-worklet.js");
    await ctx.audioWorklet.addModule("/static/playback-worklet.js");
    if (typeof ctx.setSinkId === "function") {
      gain.connect(ctx.destination);
      if (store.output) await ctx.setSinkId(store.output).catch(() => {});
    } else {
      // no AudioContext.setSinkId: route through an <audio> element instead
      const dest = new MediaStreamAudioDestinationNode(ctx);
      gain.connect(dest);
      fallbackEl = new Audio();
      fallbackEl.srcObject = dest.stream;
      fallbackEl.play().catch(() => {});
      if (store.output && fallbackEl.setSinkId)
        await fallbackEl.setSinkId(store.output).catch(() => {});
    }
    for (const name of ["ringing", "hangup", "ringtone"]) {
      const resp = await fetch(`/static/sfx/${name}.wav`);
      sfxBuf[name] = await ctx.decodeAudioData(await resp.arrayBuffer());
    }
  }

  function playSfx(name, { loop = false } = {}) {
    if (!ctx || !sfxBuf[name]) return null;
    const src = new AudioBufferSourceNode(ctx, { buffer: sfxBuf[name], loop });
    src.connect(gain);
    src.start();
    return src;
  }
  function stopRingback() { try { ringLoop?.stop(); } catch {} ringLoop = null; }

  async function setOutput(id) {
    store.output = id;
    if (!ctx) return;
    if (typeof ctx.setSinkId === "function") await ctx.setSinkId(id).catch(() => {});
    else if (fallbackEl?.setSinkId) await fallbackEl.setSinkId(id).catch(() => {});
  }

  // ---- devices -------------------------------------------------------------
  async function refreshDevices() {
    const devs = await navigator.mediaDevices.enumerateDevices();
    fill(micSel, devs.filter(d => d.kind === "audioinput"), store.input);
    fill(spkSel, devs.filter(d => d.kind === "audiooutput"), store.output);
    // hide the output row entirely where selection isn't supported (Firefox)
    spkSel.parentElement.hidden = !devs.some(d => d.kind === "audiooutput");
  }
  function fill(sel, devs, saved) {
    sel.innerHTML = "";
    for (const d of devs) {
      const o = document.createElement("option");
      o.value = d.deviceId;
      o.textContent = d.label || (sel === micSel ? "microphone" : "speaker");
      sel.appendChild(o);
    }
    if (saved && [...sel.options].some(o => o.value === saved)) sel.value = saved;
  }
  navigator.mediaDevices.addEventListener?.("devicechange", refreshDevices);

  function micConstraints() {
    return { audio: {
      channelCount: 1, echoCancellation: true, noiseSuppression: true,
      ...(store.input ? { deviceId: { ideal: store.input } } : {}) } };
  }

  // ---- mic test (meter only, mic held only while testing) ------------------
  let testStream = null, meterRaf = 0;
  micTest.addEventListener("click", async () => {
    if (testStream) return stopMicTest();
    await ensureCtx();
    testStream = await navigator.mediaDevices.getUserMedia(micConstraints())
      .catch(() => null);
    if (!testStream) return;
    micTest.textContent = "Stop"; micTest.classList.add("on");
    await refreshDevices();   // labels appear once permission is granted
    const src = ctx.createMediaStreamSource(testStream);
    const an = new AnalyserNode(ctx, { fftSize: 512 });
    src.connect(an);
    const buf = new Uint8Array(an.fftSize);
    (function tick() {
      an.getByteTimeDomainData(buf);
      let peak = 0;
      for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
      micLevel.style.width = Math.min(100, (peak / 128) * 140) + "%";
      meterRaf = requestAnimationFrame(tick);
    })();
  });
  function stopMicTest() {
    cancelAnimationFrame(meterRaf);
    micLevel.style.width = "0";
    testStream?.getTracks().forEach(t => t.stop());
    testStream = null;
    micTest.textContent = "Test"; micTest.classList.remove("on");
  }

  spkTest.addEventListener("click", async () => {
    await ensureCtx();
    playSfx("ringtone");
  });

  micSel.addEventListener("change", () => { store.input = micSel.value; });
  spkSel.addEventListener("change", () => setOutput(spkSel.value));
  vol.value = store.volume;
  vol.addEventListener("input", () => {
    store.volume = +vol.value;
    if (gain) gain.gain.value = vol.value / 100;
  });

  // ---- call machine --------------------------------------------------------
  // page states: idle | calling (claim+ws setup) | ringing | oncall | busy | unplugged
  let page = "idle", lease = null, audioWs = null, callStream = null;
  let line = { state: "idle", since: 0 };   // latest broadcast
  let timerIv = 0, captionTimeout = 0;

  function render() {
    clearInterval(timerIv); timerIv = 0;
    const set = (pillMode, btnMode, cap) => {
      if (pillMode === null) pill.hidden = true;
      else {
        pill.hidden = false;
        dot.className = "dot " + pillMode;
        pillText.textContent = pillMode === "green oncall" ? "" : pillMode; // overwritten below
      }
      btn.className = "callbtn " + btnMode;
      if (cap !== undefined) caption.textContent = cap;
    };
    if (page === "ringing") {
      pill.hidden = false; dot.className = "dot cadence"; pillText.textContent = "ringing…";
      btn.className = "callbtn red"; caption.textContent = "hang up";
    } else if (page === "oncall") {
      pill.hidden = false; dot.className = "dot green";
      btn.className = "callbtn red"; caption.textContent = "hang up";
      const t0 = Date.now();
      const tick = () => {
        const s = Math.floor((Date.now() - t0) / 1000 + oncallOffset);
        pillText.textContent =
          `on call · ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
      };
      tick(); timerIv = setInterval(tick, 1000);
    } else if (page === "busy") {
      pill.hidden = false; dot.className = "dot"; pillText.textContent = "line busy";
      btn.className = "callbtn off"; caption.textContent = "wait for their call to end";
    } else if (page === "unplugged") {
      pill.hidden = false; dot.className = "dot"; pillText.textContent = "phone unplugged";
      btn.className = "callbtn off"; caption.textContent = "not taking calls right now";
    } else { // idle
      pill.hidden = false; dot.className = "dot"; pillText.textContent = "idle";
      btn.className = "callbtn";
      if (!captionTimeout) caption.textContent = "press to call";
    }
  }
  let oncallOffset = 0;

  function toIdle(cap) {
    page = "idle"; lease = null;
    if (cap) {   // transient caption ("no answer"), fades back
      caption.textContent = cap;
      clearTimeout(captionTimeout);
      captionTimeout = setTimeout(() => {
        captionTimeout = 0;
        if (page === "idle") render();
      }, 4000);
    }
    render();
  }

  function endCallCleanup() {
    stopRingback();
    audioWs?.close(); audioWs = null;
    callStream?.getTracks().forEach(t => t.stop()); callStream = null;
  }

  async function startCall() {
    page = "calling"; btn.className = "callbtn off"; caption.textContent = "";
    try {
      await ensureCtx();
      callStream = await navigator.mediaDevices.getUserMedia(micConstraints());
      await refreshDevices();
      const r = await fetch("/call/claim", { method: "POST" });
      if (!r.ok) { endCallCleanup(); return syncFromLine(); }
      lease = (await r.json()).lease_id;

      // capture chain: mic -> 2x lowpass 3400 -> capture worklet -> ws
      const src = ctx.createMediaStreamSource(callStream);
      const lp1 = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 3400 });
      const lp2 = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 3400 });
      const cap = new AudioWorkletNode(ctx, "capture-worklet");
      src.connect(lp1).connect(lp2).connect(cap);
      playNode = new AudioWorkletNode(ctx, "playback-worklet",
                                      { outputChannelCount: [1] });
      const lp3 = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 3400 });
      playNode.connect(lp3).connect(gain);

      const proto = location.protocol === "https:" ? "wss" : "ws";
      audioWs = new WebSocket(
        `${proto}://${location.host}/ws/audio?lease=${encodeURIComponent(lease)}`);
      audioWs.binaryType = "arraybuffer";
      audioWs.onmessage = (e) => playNode.port.postMessage(e.data);
      cap.port.onmessage = (e) => {
        if (audioWs && audioWs.readyState === 1) audioWs.send(e.data);
      };
      audioWs.onclose = () => { if (page === "ringing" || page === "oncall") hangup(false); };

      await new Promise((res, rej) => {
        audioWs.onopen = res; audioWs.onerror = rej;
      });
      const rr = await fetch(`/call/ring?lease=${encodeURIComponent(lease)}`,
                             { method: "POST" });
      if (!rr.ok) { endCallCleanup(); return syncFromLine(); }
      page = "ringing";
      ringLoop = playSfx("ringing", { loop: true });
      render();
    } catch {
      endCallCleanup();
      toIdle();
    }
  }

  async function hangup(tellServer = true) {
    const l = lease;
    endCallCleanup();
    if (tellServer && l)
      fetch(`/call/hangup?lease=${encodeURIComponent(l)}`, { method: "POST" })
        .catch(() => {});
    playSfx("hangup");
    toIdle(page === "ringing" ? undefined : undefined);
    // toIdle's caption comes from line events; explicit outcomes handled in onLine
  }

  btn.addEventListener("click", () => {
    if (page === "idle") startCall();
    else if (page === "ringing" || page === "oncall") hangup(true);
    // busy / unplugged / calling: inert
  });

  // ---- events feed ---------------------------------------------------------
  function syncFromLine() {
    if (lease) return;   // my own flow drives the UI while I hold the lease
    if (line.state === "idle") { page = "idle"; render(); }
    else if (line.state === "unplugged") { page = "unplugged"; render(); }
    else { page = "busy"; render(); }
  }

  function onLine(ev) {
    const prev = line; line = ev;
    if (lease) {
      // my lease: server-side transitions I care about
      if (ev.state === "oncall" && page === "ringing") {
        stopRingback(); oncallOffset = Math.max(0, (Date.now() / 1000) - ev.since);
        page = "oncall"; render();
      } else if (ev.state === "idle") {
        const wasRinging = page === "ringing";
        endCallCleanup(); playSfx("hangup");
        toIdle(wasRinging ? "no answer" : undefined);
      }
      return;
    }
    if ((prev.state === "idle") !== (ev.state === "idle")) syncFromLine();
    else syncFromLine();
  }

  function connectEvents() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/events?feed=rt`);
    ws.onmessage = (e) => {
      let ev; try { ev = JSON.parse(e.data); } catch { return; }
      if (ev.type === "line_state") onLine(ev);
    };
    ws.onclose = () => setTimeout(connectEvents, 2000);
  }

  // ---- boot ----------------------------------------------------------------
  refreshDevices();
  render();
  connectEvents();
})();
```

- [ ] **Step 6: Run the suite**

Run: `cd hotline/server && python -m pytest -q`
Expected: all pass including `test_page_assets_served`.

- [ ] **Step 7: Manual echo-mode verification** (required before commit)

```bash
cd hotline/server
HOTLINE_ECHO=1 HOTLINE_DATA_DIR=$TMP/pp python -m hotline
```

Open `http://127.0.0.1:9100/` and verify, in order:
1. Page renders: brand left, `idle` pill right, green button, `press to call`.
2. Settings: mic test prompts for permission, meter moves, Stop releases the mic
   (browser tab mic indicator goes off). Speaker test plays the double-ring. Volume
   slider changes SFX loudness. Reload: selections and volume persist.
3. Press call: ringback loops briefly, then echo answers (echo leg answers instantly,
   so ringback may only tick once); pill shows `on call · 0:0x` counting; you hear
   yourself (echo). Hang up: hangup beep, back to `idle` + `press to call`.
4. Two tabs: tab B shows `line busy` + `wait for their call to end` while tab A calls;
   B's button inert; A hangs up, B flips to `idle` live.
5. Kill tab A mid-call: within ~15 s (grace) B flips to `idle`.

- [ ] **Step 8: Commit**

```bash
git add hotline/server/hotline/static/
git commit -m "feat(hotline): the pork phone page (states, sfx, device settings, lease client)"
```

---

### Task 7: Docs + final verification

**Files:**
- Modify: `CLAUDE.md` (hotline surface row)
- Modify: `hotline/RUNBOOK.md`
- Modify: `C:\Users\Paul\.claude\projects\C--development-mkw-split-rewrite\memory\pork-phone-hotline.md`

**Interfaces:** none (docs).

- [ ] **Step 1: Update the root `CLAUDE.md` hotline row** — append to the hotline
  surface row's description:

```
Public call page served at the subdomain root (`hotline/server/hotline/static/`,
no build step; spec docs/superpowers/specs/2026-07-21-pork-phone-public-call-page-design.md):
line lease (one caller, claim window 10s, backstop 30min), public rt events feed,
`/test` stays token-gated for benching. Yank the ATA's power to show "phone unplugged".
```

- [ ] **Step 2: Update `hotline/RUNBOOK.md`** — add a short section "Closing the line"
  documenting: unplug the ATA's **power** (not the 605 cord) to stop calls; the page
  shows "phone unplugged" within ~30 s; plugging back in reopens automatically. Also
  note the page URL and that `/test` still needs the admin token.

- [ ] **Step 3: Update the memory file** — append to the hotline memory: page shipped
  (date), the lease model one-liner, spec/plan paths, and that deploy to the Pi is
  still manual (rsync per RUNBOOK; the sparse-checkout fix `8b65d4bd` applies from the
  next tag).

- [ ] **Step 4: Full suite + manual pass**

```bash
cd hotline/server && python -m pytest -q
```

Expected: everything green. Re-run the Task 6 Step 7 manual checklist once more from a
clean browser profile (fresh permissions) to catch first-run flows.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md hotline/RUNBOOK.md
git commit -m "docs(hotline): public call page runbook + surface notes"
```

---

## Self-Review Notes

- **Spec coverage:** §2 layout/states → Task 6; §2.2 first-call → Task 6 (`startCall`
  requests the mic before claiming); §2.3 settings → Task 6; §3 SFX → Task 6 (msgin/
  msgout copied but not wired — spec says off by default unless they earn it by ear;
  wiring them is a by-ear follow-up, not in this plan); §4.1 lease → Tasks 2-4; §4.2
  events → Tasks 3-4; §4.3 unplugged → Tasks 3+5; §4.4 auth matrix → Task 4; §4.5
  failure modes → covered by lease timers (T2), grace (T3), WS close handler (T6);
  §5 file list → Tasks 1-7; §6 testing → per-task + Task 6 Step 7 / Task 7 Step 4.
- **Ring timeout in echo mode** is moot (echo answers instantly); in real mode it's
  Asterisk's originate `timeout` (Task 5), surfacing as ChannelDestroyed →
  `on_phone_hungup` → call ends → reap releases lease → page sees `idle` while it was
  `ringing` → renders "no answer". No separate server timer needed.
- **Type consistency check:** `claim_line()` sync (lease.claim is sync) — http handler
  calls it without await; `ring_with_lease`/`hangup_with_lease` async — awaited. Config
  field names match between Task 1 and consumers. `attach_caller_ws(send, lease_id=None)`
  matches http call. `line_snapshot()` used in both http hello and controller publishes.
- **Known simplification:** `render()`'s `set` helper in phone.js sketch is vestigial —
  implementer may inline/delete it; the per-state branches below it are the behavior
  spec. The page never shows a "held/calling" state to others as anything but busy
  (line_state `held` → busy), matching the spec.
