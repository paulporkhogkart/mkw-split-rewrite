# Pork Phone Off-Hook Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The public call page shows `off the hook` and refuses claims while the 802's
handset is lifted, driven by an SNMP hook-state poll of the HT802V2, recovering when
the handset is cradled.

**Architecture:** A dependency-free async SNMPv2c GET client (`hotline/snmp.py`) polls
one OID on the ATA every ~2 s from a `watch_hook` task (sibling of `watch_ata`).
It drives `Controller.set_phone_offhook()`, which composes a new `offhook` line state
(precedence `unplugged > offhook > idle`), refuses claims with 409, and broadcasts on
the public events feed like every other state. The exact OID is discovered by a
Paul-in-the-loop `snmpwalk` diff experiment at deploy; until the env keys are set the
feature is dormant and prod behavior is unchanged. Spec:
`docs/superpowers/specs/2026-07-22-pork-phone-offhook-and-earpiece-design.md`.

**Tech Stack:** Python 3.11+ asyncio, aiohttp (no new dependencies), pytest
(asyncio_mode=auto), vanilla JS page.

## Global Constraints

- Page copy: lowercase, **never em dashes** (test-enforced: `"—" not in body`).
- The internal machinery (SNMP, leases, polls) never appears in page copy.
- No new Python dependencies — the service is aiohttp-only; the SNMP client is
  hand-rolled (plan-time decision; see Task 7 spec amendment).
- Fail closed: any SNMP failure reads as on-hook (today's behavior), never a stuck
  closed line. Off-hook state may only refuse **new claims**; it never touches a live
  call.
- Zero imports from `pi/` code.
- All tests run from `hotline/server/`: `cd hotline/server && python -m pytest` (85
  pass before this plan; all must still pass after).
- Line states on the wire: `idle | held | ringing | oncall | unplugged` + new
  `offhook`. The lease id is never broadcast.
- Deploy is NOT part of this plan — it happens only on Paul's explicit go, per spec §4.

## File Structure

- `hotline/server/hotline/snmp.py` — NEW: BER codec + `snmp_get` (one responsibility:
  speak minimal SNMPv2c to one host).
- `hotline/server/hotline/config.py` — 5 new env-driven fields.
- `hotline/server/hotline/controller.py` — `_phone_offhook` flag, `PhoneOffhook`,
  snapshot composition, claim refusal.
- `hotline/server/hotline/http.py` — claim 409 `offhook`; `POST /admin/line-sim`.
- `hotline/server/hotline/__main__.py` — `watch_hook` poller + wiring.
- `hotline/server/hotline/static/phone.js` — `offhook` page state.
- `hotline/server/tests/test_snmp.py` — NEW: codec + client tests.
- `hotline/server/tests/test_offhook.py` — NEW: controller/http/state tests.
- `hotline/server/tests/test_config.py`, `tests/test_main.py` — extended.
- `hotline/RUNBOOK.md`, root `CLAUDE.md`, spec — docs (Task 7).

---

### Task 1: SNMP BER codec (pure functions)

**Files:**
- Create: `hotline/server/hotline/snmp.py`
- Test: `hotline/server/tests/test_snmp.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces (used by Task 2 and tests):
  - `class SnmpError(Exception)`
  - `MAX_PACKET: int = 1500`
  - `encode_get(community: str, oid: str, request_id: int) -> bytes`
  - `decode_response(data: bytes, request_id: int) -> tuple[int, bytes]` — returns
    `(value_tag, value_bytes)` of the first varbind; raises `SnmpError` on anything
    malformed, mismatched, or reporting an SNMP error.
  - `render_value(tag: int, body: bytes) -> str`
  - Internal helpers `_tlv(tag, body)`, `_int(n)`, `_oid(dotted)`, `class _Reader`
    (tests and Task 2's test fixtures use them to build fake responses).

- [ ] **Step 1: Write the failing tests**

Create `hotline/server/tests/test_snmp.py`:

```python
from __future__ import annotations

import pytest

from hotline.snmp import (MAX_PACKET, SnmpError, _Reader, _int, _oid, _tlv,
                          decode_response, encode_get, render_value)


# -- helpers to build responses the way a real agent would -------------------

def make_response(request_id: int, value_tlv: bytes,
                  oid: str = "1.3.6.1.4.1.1", err: int = 0,
                  pdu_tag: int = 0xA2) -> bytes:
    varbind = _tlv(0x30, _oid(oid) + value_tlv)
    pdu = _tlv(pdu_tag, _int(request_id) + _int(err) + _int(0)
               + _tlv(0x30, varbind))
    return _tlv(0x30, _int(1) + _tlv(0x04, b"pub") + pdu)


# -- OID encoding (golden values) --------------------------------------------

def test_oid_golden_sysdescr():
    # 1.3.6.1.2.1.1.1.0 is the canonical example: 1.3 packs to 0x2B
    assert _oid("1.3.6.1.2.1.1.1.0") == bytes.fromhex("06082b060102010101" + "00")


def test_oid_multibyte_arc():
    # 42397 = 0x82 0xCB 0x1D in base-128 with continuation bits
    assert _oid("1.3.6.1.4.1.42397").endswith(bytes([0x82, 0xCB, 0x1D]))


def test_oid_rejects_too_short():
    with pytest.raises(SnmpError):
        _oid("1")


# -- request encoding, verified by walking it with our own reader ------------

def test_encode_get_structure():
    pkt = encode_get("secret", "1.3.6.1.2.1.1.1.0", request_id=1234)
    msg = _Reader(pkt)
    tag, body = msg.tlv()
    assert tag == 0x30 and msg.pos == len(pkt)     # one top-level message
    inner = _Reader(body)
    vtag, vbody = inner.tlv()
    assert (vtag, vbody) == (0x02, b"\x01")        # version = 1 (v2c)
    ctag, cbody = inner.tlv()
    assert (ctag, cbody) == (0x04, b"secret")
    ptag, pdu = inner.tlv()
    assert ptag == 0xA0                            # GetRequest
    p = _Reader(pdu)
    rtag, rbody = p.tlv()
    assert int.from_bytes(rbody, "big", signed=True) == 1234


# -- response decoding --------------------------------------------------------

def test_decode_happy_integer_value():
    data = make_response(77, _int(2))
    tag, body = decode_response(data, 77)
    assert render_value(tag, body) == "2"


def test_decode_rejects_wrong_request_id():
    with pytest.raises(SnmpError):
        decode_response(make_response(77, _int(2)), 78)


def test_decode_rejects_snmp_error_status():
    with pytest.raises(SnmpError):
        decode_response(make_response(77, _int(2), err=5), 77)


def test_decode_rejects_wrong_pdu_tag():
    with pytest.raises(SnmpError):    # a GetRequest is not a response
        decode_response(make_response(77, _int(2), pdu_tag=0xA0), 77)


def test_decode_rejects_truncated():
    data = make_response(77, _int(2))
    with pytest.raises(SnmpError):
        decode_response(data[:-3], 77)


def test_decode_rejects_length_bomb():
    # long-form length claiming 5 length bytes is refused outright
    evil = bytes([0x30, 0x85, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]) + b"x" * 40
    with pytest.raises(SnmpError):
        decode_response(evil, 77)


def test_decode_rejects_oversized_packet():
    with pytest.raises(SnmpError):
        decode_response(b"\x30\x03\x02\x01\x01" + b"x" * MAX_PACKET, 77)


def test_decode_rejects_garbage():
    for junk in (b"", b"\x00", b"\xff" * 30, b"not snmp at all"):
        with pytest.raises(SnmpError):
            decode_response(junk, 77)


# -- value rendering ----------------------------------------------------------

def test_render_int_string_and_hex():
    assert render_value(0x02, b"\x02") == "2"
    assert render_value(0x04, b"Off-Hook") == "Off-Hook"
    assert render_value(0x43, b"\x01\x02") == "0x0102"   # TimeTicks -> hex
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd hotline/server && python -m pytest tests/test_snmp.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hotline.snmp'`

- [ ] **Step 3: Write the codec**

Create `hotline/server/hotline/snmp.py`:

```python
"""Minimal SNMPv2c GET client + BER codec.

Hand-rolled on purpose: the service polls ONE OID from ONE device on an
isolated VLAN. ~100 lines of BER we fully control (and fuzz in tests) is a
smaller in-process attack surface than a general SNMP stack parsing
untrusted UDP. The socket is connect()ed to the ATA so the kernel drops
frames from any other source; request ids are verified end-to-end; anything
malformed raises SnmpError and the poller fails closed (reads as on-hook).
"""
from __future__ import annotations

import asyncio
import secrets

MAX_PACKET = 1500


class SnmpError(Exception):
    pass


# -- BER encoding -------------------------------------------------------------

def _len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _tlv(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _len(len(body)) + body


def _int(n: int) -> bytes:
    body = n.to_bytes(max(1, (n.bit_length() + 8) // 8), "big", signed=True)
    return _tlv(0x02, body)


def _oid(dotted: str) -> bytes:
    arcs = [int(a) for a in dotted.strip(".").split(".") if a != ""]
    if len(arcs) < 2:
        raise SnmpError("oid too short")
    body = bytearray([40 * arcs[0] + arcs[1]])
    for arc in arcs[2:]:
        chunk = bytearray([arc & 0x7F])
        arc >>= 7
        while arc:
            chunk.insert(0, 0x80 | (arc & 0x7F))
            arc >>= 7
        body += chunk
    return _tlv(0x06, bytes(body))


def encode_get(community: str, oid: str, request_id: int) -> bytes:
    varbind = _tlv(0x30, _oid(oid) + _tlv(0x05, b""))          # OID + NULL
    pdu = _tlv(0xA0, _int(request_id) + _int(0) + _int(0)      # GetRequest
               + _tlv(0x30, varbind))
    return _tlv(0x30, _int(1) + _tlv(0x04, community.encode()) + pdu)


# -- BER decoding (defensive: every length is bounds-checked) -----------------

class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def tlv(self) -> tuple[int, bytes]:
        if self.pos + 2 > len(self.data):
            raise SnmpError("truncated")
        tag = self.data[self.pos]
        first = self.data[self.pos + 1]
        self.pos += 2
        if first < 0x80:
            length = first
        else:
            n = first & 0x7F
            if not 1 <= n <= 4:
                raise SnmpError("bad length form")
            if self.pos + n > len(self.data):
                raise SnmpError("truncated")
            length = int.from_bytes(self.data[self.pos:self.pos + n], "big")
            self.pos += n
        if self.pos + length > len(self.data):
            raise SnmpError("truncated")
        body = self.data[self.pos:self.pos + length]
        self.pos += length
        return tag, body


def _read_int(pair: tuple[int, bytes]) -> int:
    tag, body = pair
    if tag != 0x02 or not body or len(body) > 8:
        raise SnmpError("expected int")
    return int.from_bytes(body, "big", signed=True)


def decode_response(data: bytes, request_id: int) -> tuple[int, bytes]:
    """(value_tag, value_bytes) of the first varbind, or SnmpError."""
    if len(data) > MAX_PACKET:
        raise SnmpError("oversized")
    tag, body = _Reader(data).tlv()
    if tag != 0x30:
        raise SnmpError("not a message")
    msg = _Reader(body)
    _read_int(msg.tlv())                       # version
    ctag, _community = msg.tlv()
    if ctag != 0x04:
        raise SnmpError("bad community field")
    ptag, pdu_body = msg.tlv()
    if ptag != 0xA2:                           # GetResponse only
        raise SnmpError("not a response pdu")
    pdu = _Reader(pdu_body)
    if _read_int(pdu.tlv()) != request_id:
        raise SnmpError("request id mismatch")
    if _read_int(pdu.tlv()) != 0:              # error-status
        raise SnmpError("snmp error status")
    _read_int(pdu.tlv())                       # error-index
    vtag, vb_list = pdu.tlv()
    if vtag != 0x30:
        raise SnmpError("bad varbind list")
    v1tag, v1body = _Reader(vb_list).tlv()
    if v1tag != 0x30:
        raise SnmpError("bad varbind")
    one = _Reader(v1body)
    otag, _oid_body = one.tlv()
    if otag != 0x06:
        raise SnmpError("bad oid")
    return one.tlv()


def render_value(tag: int, body: bytes) -> str:
    """String form for comparing against configured off-hook values."""
    if tag == 0x02 and body and len(body) <= 8:
        return str(int.from_bytes(body, "big", signed=True))
    if tag == 0x04:
        return body.decode("utf-8", "replace")
    return "0x" + body.hex()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd hotline/server && python -m pytest tests/test_snmp.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add hotline/server/hotline/snmp.py hotline/server/tests/test_snmp.py
git commit -m "feat(hotline): minimal SNMPv2c BER codec, defensively parsed"
```

---

### Task 2: async `snmp_get` against a fake UDP agent

**Files:**
- Modify: `hotline/server/hotline/snmp.py` (append)
- Test: `hotline/server/tests/test_snmp.py` (append)

**Interfaces:**
- Consumes: Task 1's codec.
- Produces: `async snmp_get(host: str, community: str, oid: str, port: int = 161,
  timeout_s: float = 2.0) -> str` — returns `render_value(...)` of the reply; raises
  `SnmpError`/`asyncio.TimeoutError`/`OSError` on failure. Task 6's poller calls it.

- [ ] **Step 1: Write the failing tests**

Append to `hotline/server/tests/test_snmp.py`:

```python
# -- async client against a fake UDP agent ------------------------------------

import asyncio

from hotline.snmp import snmp_get


def request_id_of(packet: bytes) -> int:
    msg = _Reader(packet)
    _tag, body = msg.tlv()
    inner = _Reader(body)
    inner.tlv()                                   # version
    inner.tlv()                                   # community
    _ptag, pdu = inner.tlv()
    _rtag, rbody = _Reader(pdu).tlv()
    return int.from_bytes(rbody, "big", signed=True)


class FakeAgent(asyncio.DatagramProtocol):
    """reply_fn(request_packet) -> list of datagrams to send back."""

    def __init__(self, reply_fn) -> None:
        self.reply_fn = reply_fn

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data, addr) -> None:
        for out in self.reply_fn(data):
            self.transport.sendto(out, addr)


async def start_agent(reply_fn):
    loop = asyncio.get_running_loop()
    transport, _proto = await loop.create_datagram_endpoint(
        lambda: FakeAgent(reply_fn), local_addr=("127.0.0.1", 0))
    return transport, transport.get_extra_info("sockname")[1]


async def test_snmp_get_returns_value():
    transport, port = await start_agent(
        lambda req: [make_response(request_id_of(req), _int(2))])
    try:
        assert await snmp_get("127.0.0.1", "pub", "1.3.6.1.4.1.1",
                              port=port, timeout_s=2.0) == "2"
    finally:
        transport.close()


async def test_snmp_get_skips_garbage_then_accepts_valid():
    # a spoofer racing garbage in first must not break the poll
    transport, port = await start_agent(
        lambda req: [b"\xff\xff\xff", make_response(9999, _int(1)),
                     make_response(request_id_of(req), _int(2))])
    try:
        assert await snmp_get("127.0.0.1", "pub", "1.3.6.1.4.1.1",
                              port=port, timeout_s=2.0) == "2"
    finally:
        transport.close()


async def test_snmp_get_times_out_when_silent():
    transport, port = await start_agent(lambda req: [])
    try:
        with pytest.raises(asyncio.TimeoutError):
            await snmp_get("127.0.0.1", "pub", "1.3.6.1.4.1.1",
                           port=port, timeout_s=0.2)
    finally:
        transport.close()


async def test_snmp_get_times_out_on_wrong_request_id_only():
    transport, port = await start_agent(
        lambda req: [make_response(request_id_of(req) + 1, _int(2))])
    try:
        with pytest.raises(asyncio.TimeoutError):
            await snmp_get("127.0.0.1", "pub", "1.3.6.1.4.1.1",
                           port=port, timeout_s=0.2)
    finally:
        transport.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd hotline/server && python -m pytest tests/test_snmp.py -v`
Expected: the four new tests FAIL — `ImportError: cannot import name 'snmp_get'`.

- [ ] **Step 3: Implement `snmp_get`**

Append to `hotline/server/hotline/snmp.py`:

```python
# -- async client -------------------------------------------------------------

class _ClientProto(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr) -> None:
        self.queue.put_nowait(data)


async def snmp_get(host: str, community: str, oid: str,
                   port: int = 161, timeout_s: float = 2.0) -> str:
    """One SNMPv2c GET. The socket is connect()ed to (host, port), so the
    kernel drops datagrams from any other source; garbage or mismatched
    request ids are skipped until the deadline (a spoofer can't break the
    poll by racing packets in)."""
    request_id = secrets.randbelow(0x7FFFFFFE) + 1
    packet = encode_get(community, oid, request_id)
    loop = asyncio.get_running_loop()
    transport, proto = await loop.create_datagram_endpoint(
        _ClientProto, remote_addr=(host, port))
    try:
        transport.sendto(packet)
        deadline = loop.time() + timeout_s
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            data = await asyncio.wait_for(proto.queue.get(), remaining)
            try:
                tag, body = decode_response(data, request_id)
            except SnmpError:
                continue
            return render_value(tag, body)
    finally:
        transport.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd hotline/server && python -m pytest tests/test_snmp.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add hotline/server/hotline/snmp.py hotline/server/tests/test_snmp.py
git commit -m "feat(hotline): async snmp_get with connected socket + spoof-tolerant wait"
```

---

### Task 3: Controller off-hook state

**Files:**
- Modify: `hotline/server/hotline/controller.py`
- Test: create `hotline/server/tests/test_offhook.py`

**Interfaces:**
- Consumes: existing `Controller` internals (`_phone_reachable`, `lease`, `bus`).
- Produces (Tasks 4 and 6 rely on these exact names):
  - `class PhoneOffhook(Exception)` in `hotline.controller`
  - `Controller.set_phone_offhook(offhook: bool) -> None`
  - `Controller.line_snapshot()` may now return `state: "offhook"`
  - `Controller.claim_line()` raises `PhoneOffhook` (after the `PhoneUnplugged` check)

- [ ] **Step 1: Write the failing tests**

Create `hotline/server/tests/test_offhook.py`:

```python
from __future__ import annotations

import asyncio

from hotline.config import Config
from hotline.controller import Controller, PhoneOffhook, PhoneUnplugged
from hotline.db import Db
from hotline.events import EventBus


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
    return cfg, bus, db, ctl


async def close_stack(ctl, bus, db):
    await ctl.stop(); await bus.stop(); db.close()


async def test_offhook_composes_into_snapshot(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_stack(tmp_path, unused_tcp_port)
    assert ctl.line_snapshot()["state"] == "idle"
    ctl.set_phone_offhook(True)
    assert ctl.line_snapshot()["state"] == "offhook"
    ctl.set_phone_offhook(False)
    assert ctl.line_snapshot()["state"] == "idle"
    await close_stack(ctl, bus, db)


async def test_unplugged_wins_over_offhook(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_stack(tmp_path, unused_tcp_port)
    ctl.set_phone_offhook(True)
    ctl.set_phone_reachable(False)
    assert ctl.line_snapshot()["state"] == "unplugged"
    ctl.set_phone_reachable(True)
    assert ctl.line_snapshot()["state"] == "offhook"
    await close_stack(ctl, bus, db)


async def test_offhook_change_publishes_line_state(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_stack(tmp_path, unused_tcp_port)
    q = bus.subscribe("rt")
    ctl.set_phone_offhook(True)
    ctl.set_phone_offhook(True)          # no-op: must not publish twice
    ev = await asyncio.wait_for(q.get(), 2)
    assert ev["type"] == "line_state" and ev["state"] == "offhook"
    await asyncio.sleep(0.05)            # give a wrongly-published dup time to land
    assert q.empty()
    bus.unsubscribe("rt", q)
    await close_stack(ctl, bus, db)


async def test_claim_refused_while_offhook(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_stack(tmp_path, unused_tcp_port)
    ctl.set_phone_offhook(True)
    try:
        ctl.claim_line()
        raise AssertionError("claim should have raised")
    except PhoneOffhook:
        pass
    ctl.set_phone_offhook(False)
    assert ctl.claim_line()              # line claimable again
    await close_stack(ctl, bus, db)


async def test_unplugged_raised_before_offhook(tmp_path, unused_tcp_port):
    _, bus, db, ctl = await make_stack(tmp_path, unused_tcp_port)
    ctl.set_phone_offhook(True)
    ctl.set_phone_reachable(False)
    try:
        ctl.claim_line()
        raise AssertionError("claim should have raised")
    except PhoneUnplugged:
        pass
    await close_stack(ctl, bus, db)


async def test_lease_states_render_over_offhook(tmp_path, unused_tcp_port):
    # during a real call the hook is naturally off; the lease state must win
    _, bus, db, ctl = await make_stack(tmp_path, unused_tcp_port)
    lease = ctl.claim_line()
    ctl.set_phone_offhook(True)
    assert ctl.line_snapshot()["state"] == "held"
    ctl.lease.release(lease)
    assert ctl.line_snapshot()["state"] == "offhook"
    await close_stack(ctl, bus, db)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd hotline/server && python -m pytest tests/test_offhook.py -v`
Expected: FAIL — `ImportError: cannot import name 'PhoneOffhook'`.

- [ ] **Step 3: Implement in `controller.py`**

Below the existing `PhoneUnplugged` class add:

```python
class PhoneOffhook(Exception):
    pass
```

In `Controller.__init__`, next to `self._phone_reachable = True` add:

```python
        self._phone_offhook = False    # driven by the SNMP hook poll (real
                                       # mode) or /admin/line-sim (bench)
```

Replace `line_snapshot` with:

```python
    def line_snapshot(self) -> dict:
        snap = self.lease.snapshot()
        if snap["state"] == "idle":
            if not self._phone_reachable:
                snap["state"] = "unplugged"
            elif self._phone_offhook:
                snap["state"] = "offhook"
        return snap
```

Below `set_phone_reachable` add (same shape):

```python
    def set_phone_offhook(self, offhook: bool) -> None:
        if offhook == self._phone_offhook:
            return
        self._phone_offhook = offhook
        self.bus.publish(self.line_snapshot())
```

Replace `claim_line` with:

```python
    def claim_line(self) -> str:
        if not self._phone_reachable:
            raise PhoneUnplugged()
        if self._phone_offhook:
            raise PhoneOffhook()
        return self.lease.claim()
```

- [ ] **Step 4: Run the tests**

Run: `cd hotline/server && python -m pytest tests/test_offhook.py tests/test_controller_lease.py -v`
Expected: all PASS (existing lease tests must not regress).

- [ ] **Step 5: Commit**

```bash
git add hotline/server/hotline/controller.py hotline/server/tests/test_offhook.py
git commit -m "feat(hotline): offhook line state - composition, publish, claim refusal"
```

---

### Task 4: HTTP surface — claim 409 + `/admin/line-sim`

**Files:**
- Modify: `hotline/server/hotline/http.py`
- Test: `hotline/server/tests/test_offhook.py` (append)

**Interfaces:**
- Consumes: Task 3's `PhoneOffhook`, `set_phone_offhook`.
- Produces: `POST /call/claim` → `409 {"error": "offhook"}`;
  `POST /admin/line-sim?state=offhook|clear&token=...` → `{"ok": true}` (401 without
  token, 400 on bad state). The page (Task 5) and RUNBOOK (Task 7) rely on these.

- [ ] **Step 1: Write the failing tests**

Append to `hotline/server/tests/test_offhook.py`:

```python
# -- HTTP surface -------------------------------------------------------------

from aiohttp.test_utils import TestClient, TestServer

from hotline.http import make_app


async def make_http_stack(tmp_path, unused_tcp_port, **env):
    cfg, bus, db, ctl = await make_stack(tmp_path, unused_tcp_port, **env)
    client = TestClient(TestServer(make_app(cfg, ctl)))
    await client.start_server()
    return cfg, bus, db, ctl, client


async def test_claim_409s_offhook(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_http_stack(tmp_path, unused_tcp_port)
    ctl.set_phone_offhook(True)
    resp = await client.post("/call/claim")
    assert resp.status == 409 and (await resp.json())["error"] == "offhook"
    await client.close(); await close_stack(ctl, bus, db)


async def test_line_sim_drives_offhook(tmp_path, unused_tcp_port):
    _, bus, db, ctl, client = await make_http_stack(tmp_path, unused_tcp_port)
    assert (await client.post("/admin/line-sim?state=offhook")).status == 401
    resp = await client.post("/admin/line-sim?state=offhook&token=dev-token")
    assert resp.status == 200
    assert ctl.line_snapshot()["state"] == "offhook"
    resp = await client.post("/admin/line-sim?state=clear&token=dev-token")
    assert resp.status == 200
    assert ctl.line_snapshot()["state"] == "idle"
    assert (await client.post(
        "/admin/line-sim?state=bogus&token=dev-token")).status == 400
    await client.close(); await close_stack(ctl, bus, db)


async def test_events_hello_reports_offhook(tmp_path, unused_tcp_port):
    import asyncio as aio
    _, bus, db, ctl, client = await make_http_stack(tmp_path, unused_tcp_port)
    ctl.set_phone_offhook(True)
    ws = await client.ws_connect("/ws/events?feed=rt")
    hello = await aio.wait_for(ws.receive_json(), 5)
    assert hello["type"] == "line_state" and hello["state"] == "offhook"
    await ws.close()
    await client.close(); await close_stack(ctl, bus, db)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd hotline/server && python -m pytest tests/test_offhook.py -v`
Expected: `test_claim_409s_offhook` FAILS with status 500 (unhandled `PhoneOffhook`),
`test_line_sim_drives_offhook` FAILS with 404 (no route). The hello test may already
pass (composition landed in Task 3) — that's fine.

- [ ] **Step 3: Implement in `http.py`**

Change the controller import line to:

```python
from .controller import PhoneOffhook, PhoneUnplugged
```

In `_call_claim`, add an except arm after the `PhoneUnplugged` one:

```python
    except PhoneOffhook:
        return web.json_response({"error": "offhook"}, status=409)
```

Add the handler next to `_hangup`:

```python
async def _line_sim(request: web.Request) -> web.Response:
    """Bench/debug: drive the offhook flag by hand (echo mode has no SNMP).
    In real mode the poller re-asserts truth on its next tick."""
    if not _authed(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    state = request.query.get("state", "")
    if state not in ("offhook", "clear"):
        return web.json_response({"error": "bad state"}, status=400)
    request.app[CONTROLLER_KEY].set_phone_offhook(state == "offhook")
    return web.json_response({"ok": True})
```

Register it in `make_app` next to the other admin routes:

```python
    app.router.add_post("/admin/line-sim", _line_sim)
```

- [ ] **Step 4: Run the tests**

Run: `cd hotline/server && python -m pytest tests/test_offhook.py tests/test_public_api.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add hotline/server/hotline/http.py hotline/server/tests/test_offhook.py
git commit -m "feat(hotline): claim 409 offhook + /admin/line-sim bench endpoint"
```

---

### Task 5: Page `offhook` state

**Files:**
- Modify: `hotline/server/hotline/static/phone.js`

**Interfaces:**
- Consumes: `line_state` broadcasts with `state: "offhook"` (Task 3/4); the claim-409
  path needs no change (`!r.ok` already plays `busy.wav` then `syncFromLine()`, which
  now lands on the offhook rendering — dialling an off-hook line giving a busy tone is
  correct phone behavior).
- Produces: page state `offhook` — pill grey dot `off the hook`, disabled button,
  caption `the phone is off the hook`, no sound.

- [ ] **Step 1: Add the state to the page comment**

In `phone.js`, change the call-machine comment line:

```js
  // page states: idle | calling (claim+ws setup) | ringing | oncall | busy | unplugged
```

to:

```js
  // page states: idle | calling (claim+ws setup) | ringing | oncall | busy |
  // unplugged | offhook
```

- [ ] **Step 2: Render branch**

In `render()`, insert between the `busy` and `unplugged` branches:

```js
    } else if (page === "offhook") {
      pill.hidden = false; dot.className = "dot"; pillText.textContent = "off the hook";
      btn.className = "callbtn off"; caption.textContent = "the phone is off the hook";
```

- [ ] **Step 3: Feed mapping**

In `syncFromLine()`, insert before the final `else` branch:

```js
    else if (line.state === "offhook") { page = "offhook"; render(); }
```

so the function reads idle → unplugged → offhook → else-busy.

- [ ] **Step 4: Manual verification in the echo-mode dev loop**

Run (PowerShell, from repo root; use a scratch data dir):

```powershell
$env:HOTLINE_ECHO="1"; $env:HOTLINE_ECHO_RING_S="4"; $env:HOTLINE_DATA_DIR="$env:TEMP\hotline-dev"; python -m hotline
```

Open `http://127.0.0.1:9100/`, then in a second terminal:

```bash
curl -X POST "http://127.0.0.1:9100/admin/line-sim?state=offhook&token=dev-token"
```

Expected: pill flips to `off the hook`, button recessed/disabled, caption
`the phone is off the hook`. Press the (disabled) button — nothing happens. Then:

```bash
curl -X POST "http://127.0.0.1:9100/admin/line-sim?state=clear&token=dev-token"
```

Expected: page returns to `idle` / `press to call`, and a full echo call still works
end to end. Also verify the copy has no em dashes and is lowercase.

- [ ] **Step 5: Run the copy guard + commit**

Run: `cd hotline/server && python -m pytest tests/test_public_api.py -v`
Expected: PASS (em-dash guard included).

```bash
git add hotline/server/hotline/static/phone.js
git commit -m "feat(hotline): off the hook page state"
```

---

### Task 6: Config keys + `watch_hook` poller + wiring

**Files:**
- Modify: `hotline/server/hotline/config.py`
- Modify: `hotline/server/hotline/__main__.py`
- Test: `hotline/server/tests/test_config.py` (append),
  `hotline/server/tests/test_main.py` (append)

**Interfaces:**
- Consumes: Task 2's `snmp_get`, Task 3's `set_phone_offhook`.
- Produces:
  - `Config` fields: `snmp_host: str`, `snmp_community: str`, `snmp_hook_oid: str`,
    `snmp_offhook_values: tuple[str, ...]`, `snmp_poll_s: float`
  - `async watch_hook(cfg: Config, controller: Controller, stop: asyncio.Event)`
    in `hotline.__main__`
  - Poller runs only when `not echo_mode` and host+community+oid+values all set.

- [ ] **Step 1: Write the failing config tests**

Append to `hotline/server/tests/test_config.py` (match the file's existing style —
it builds `Config.from_env` from dicts):

```python
def test_snmp_defaults_disabled(tmp_path):
    cfg = Config.from_env({"HOTLINE_ENV": "dev",
                           "HOTLINE_DATA_DIR": str(tmp_path)})
    assert cfg.snmp_host == ""
    assert cfg.snmp_community == ""
    assert cfg.snmp_hook_oid == ""
    assert cfg.snmp_offhook_values == ()
    assert cfg.snmp_poll_s == 2.0


def test_snmp_values_parse(tmp_path):
    cfg = Config.from_env({
        "HOTLINE_ENV": "dev", "HOTLINE_DATA_DIR": str(tmp_path),
        "HOTLINE_SNMP_HOST": "192.168.3.226",
        "HOTLINE_SNMP_COMMUNITY": "s3cret",
        "HOTLINE_SNMP_HOOK_OID": "1.3.6.1.4.1.42397.1.2.1.1.3.1",
        "HOTLINE_SNMP_OFFHOOK_VALUES": "2, Off-Hook",
        "HOTLINE_SNMP_POLL_S": "1.5",
    })
    assert cfg.snmp_host == "192.168.3.226"
    assert cfg.snmp_offhook_values == ("2", "Off-Hook")
    assert cfg.snmp_poll_s == 1.5
```

- [ ] **Step 2: Run to verify failure**

Run: `cd hotline/server && python -m pytest tests/test_config.py -v`
Expected: the two new tests FAIL — `Config` has no `snmp_host`.

- [ ] **Step 3: Add the config fields**

In `config.py`, append to the dataclass fields (after `echo_ring_s: float`):

```python
    snmp_host: str
    snmp_community: str
    snmp_hook_oid: str
    snmp_offhook_values: tuple[str, ...]
    snmp_poll_s: float
```

and to the `from_env` constructor call (after `echo_ring_s=...`):

```python
            snmp_host=environ.get("HOTLINE_SNMP_HOST", ""),
            snmp_community=environ.get("HOTLINE_SNMP_COMMUNITY", ""),
            snmp_hook_oid=environ.get("HOTLINE_SNMP_HOOK_OID", ""),
            snmp_offhook_values=tuple(
                v.strip() for v in
                environ.get("HOTLINE_SNMP_OFFHOOK_VALUES", "").split(",")
                if v.strip()),
            snmp_poll_s=float(environ.get("HOTLINE_SNMP_POLL_S", "2")),
```

- [ ] **Step 4: Run config tests**

Run: `cd hotline/server && python -m pytest tests/test_config.py -v`
Expected: all PASS.

- [ ] **Step 5: Write the failing poller test**

Append to `hotline/server/tests/test_main.py`:

```python
async def test_watch_hook_polls_and_fails_closed(tmp_path, monkeypatch):
    import hotline.__main__ as main_mod
    from hotline.snmp import SnmpError

    cfg = Config.from_env({
        "HOTLINE_ENV": "dev", "HOTLINE_DATA_DIR": str(tmp_path),
        "HOTLINE_SNMP_HOST": "192.0.2.1", "HOTLINE_SNMP_COMMUNITY": "pub",
        "HOTLINE_SNMP_HOOK_OID": "1.3.6.1.4.1.1",
        "HOTLINE_SNMP_OFFHOOK_VALUES": "2",
        "HOTLINE_SNMP_POLL_S": "0.02",
    })

    import itertools
    # infinite tail: the poller may tick again before stop.set() lands, and an
    # exhausted iterator would raise StopIteration -> RuntimeError in the task
    values = itertools.chain(["1", "2", "2", SnmpError("boom"), "1"],
                             itertools.repeat("1"))
    seen: list[bool] = []

    async def fake_get(host, community, oid, port=161, timeout_s=2.0):
        v = next(values)
        if isinstance(v, Exception):
            raise v
        return v

    class FakeController:
        def set_phone_offhook(self, offhook: bool) -> None:
            seen.append(offhook)

    monkeypatch.setattr(main_mod, "snmp_get", fake_get)
    stop = asyncio.Event()
    task = asyncio.create_task(
        main_mod.watch_hook(cfg, FakeController(), stop))
    while len(seen) < 5:
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, 2)
    # "2" is the configured off-hook value; the error tick fails closed
    assert seen[:5] == [False, True, True, False, False]
```

- [ ] **Step 6: Run to verify failure**

Run: `cd hotline/server && python -m pytest tests/test_main.py -v`
Expected: new test FAILS — `hotline.__main__` has no `watch_hook`.

- [ ] **Step 7: Implement `watch_hook` + wiring**

In `__main__.py`, add to the imports:

```python
from .snmp import SnmpError, snmp_get
```

Add below `watch_ata`:

```python
async def watch_hook(cfg: Config, controller: Controller,
                     stop: asyncio.Event) -> None:
    """Poll the ATA's hook-state OID; drive the line's offhook state.
    Any failure reads as on-hook -- fail closed to today's behavior."""
    offhook_values = set(cfg.snmp_offhook_values)
    while not stop.is_set():
        offhook = False
        try:
            value = await snmp_get(cfg.snmp_host, cfg.snmp_community,
                                   cfg.snmp_hook_oid,
                                   timeout_s=min(cfg.snmp_poll_s, 2.0))
            offhook = value in offhook_values
        except (SnmpError, asyncio.TimeoutError, OSError):
            pass
        controller.set_phone_offhook(offhook)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), cfg.snmp_poll_s)
```

In `build_and_run`, after the `watch_task` block add:

```python
    hook_task: Optional[asyncio.Task] = None
    if (not cfg.echo_mode and cfg.snmp_host and cfg.snmp_community
            and cfg.snmp_hook_oid and cfg.snmp_offhook_values):
        hook_task = asyncio.create_task(watch_hook(cfg, controller, stop))
```

and in the teardown, mirror `watch_task` right after it:

```python
    if hook_task:
        hook_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hook_task
```

- [ ] **Step 8: Run the full suite**

Run: `cd hotline/server && python -m pytest -q`
Expected: everything passes (85 pre-existing + all new).

- [ ] **Step 9: Commit**

```bash
git add hotline/server/hotline/config.py hotline/server/hotline/__main__.py \
        hotline/server/tests/test_config.py hotline/server/tests/test_main.py
git commit -m "feat(hotline): SNMP hook poller, env-gated, fail-closed"
```

---

### Task 7: Docs + spec amendment + final verification

**Files:**
- Modify: `hotline/RUNBOOK.md`
- Modify: root `CLAUDE.md` (hotline surface row)
- Modify: `docs/superpowers/specs/2026-07-22-pork-phone-offhook-and-earpiece-design.md`

**Interfaces:**
- Consumes: everything above; the env key names and `/admin/line-sim` exactly as
  built.
- Produces: the operator-facing truth Paul follows at deploy.

- [ ] **Step 1: RUNBOOK — append the off-hook section**

Append to `hotline/RUNBOOK.md`:

```markdown
## Off-hook detection (SNMP hook poll)

The page shows `off the hook` and refuses calls while the handset is lifted.
Driven by an SNMPv2c poll of the ATA every 2 s. Feature is DORMANT until the
env keys below are set.

### One-time: enable SNMP on the ATA (V2 admin UI)

System Settings → scroll to the **SNMP Settings** block (just after TR-069):

1. Enable SNMP: **Yes** · SNMP Version: **Version 2c** · SNMP Port: **161**
2. SNMPv1/v2c Community: paste a long random string (this is a read password;
   generate with `openssl rand -hex 16`)
3. Leave ALL trap fields empty (Trap IP Address blank = no traps)
4. Apply / reboot if prompted

### One-time: find the hook OID (the gating experiment)

From the Pi (`ssh pi@192.168.4.21`):

    sudo apt install snmp
    # handset CRADLED:
    snmpwalk -v2c -c <community> -On 192.168.3.226 > /tmp/onhook.txt
    # lift the handset (no call), then:
    snmpwalk -v2c -c <community> -On 192.168.3.226 > /tmp/offhook.txt
    diff /tmp/onhook.txt /tmp/offhook.txt

The OID that flips is the signal. Note its off-hook value(s). Re-check it flips
during a live call and after the far side hangs up while the handset stays up.
AUDIT the walk output for anything secret-looking (SIP passwords, server
addresses) before keeping files; delete both files after extracting the OID.
If nothing hook-shaped flips: the SNMP approach is dead on this firmware, use
the spec's §1.8 auto-dial fallback instead.

### Enable on the Pi

Append to `/etc/hotline/hotline.env` (values from the experiment):

    HOTLINE_SNMP_HOST=192.168.3.226
    HOTLINE_SNMP_COMMUNITY=<community>
    HOTLINE_SNMP_HOOK_OID=<oid from the diff, numeric form>
    HOTLINE_SNMP_OFFHOOK_VALUES=<value(s) meaning off-hook, comma-separated>

then `sudo systemctl restart hotline`.

### Physical test matrix

T1 lift idle → page `off the hook` within ~3 s · T2 cradle → `idle` ·
T3 web call rings → lift → answers normally, two-way audio · T4 lift during
the claim/ring race → caller fails fast, page recovers to `off the hook` ·
T5 off-hook then ATA power-yank → `phone unplugged` · T6 leave off-hook
30 min → state holds.

### Bench/debug

`POST /admin/line-sim?state=offhook|clear&token=<admin>` drives the state by
hand (works in echo mode; in real mode the poller re-asserts truth on its
next tick).
```

- [ ] **Step 2: CLAUDE.md — extend the hotline surface row**

In the root `CLAUDE.md` hotline row, after the sentence about yanking the ATA's
power, append:

```
Off-hook detection: SNMP hook poll of the ATA (env-gated `HOTLINE_SNMP_*`; page shows "off the hook", claims 409) — spec docs/superpowers/specs/2026-07-22-pork-phone-offhook-and-earpiece-design.md.
```

- [ ] **Step 3: Spec amendment — record the plan-time client decision**

In the spec's §1.3 bullet about the ATA settings, the "v3 with authPriv preferred"
sentence is superseded by the plan-time decision. Replace that bullet's credential
sentences with:

```markdown
  Enable SNMP, port 161, **v2c with a strong random community** (plan-time decision:
  the client is hand-rolled and dependency-free — a defensively parsed ~100-line BER
  codec we control beats pulling a full SNMP stack into the process for one read-only
  OID; v2c's plaintext community is tolerable on the isolated Phone VLAN with pinned
  host IPs, same posture as SIP). **v3 authPriv via a vetted library becomes the
  mandatory upgrade if the §1.4 walk audit finds secrets in the MIB.** Credentials
  live with the other secrets in `/etc/hotline/hotline.env`. **Trap destinations stay
  empty** (unused surface).
```

Also update §1.10 item 2's first sentence from "v3 authPriv preferred (encrypted +
authenticated, the concern disappears); the v2c fallback's plaintext community..."
to:

```markdown
   **Credential on the wire.** v2c ships (see §1.3 plan-time decision); its plaintext
   community crosses only the Phone VLAN and the WPA3/AES inter-building bridge, is
   readable only from inside those links, and buys read-only access. v3 authPriv via
   a vetted library is the mandatory upgrade if the walk audit finds secrets. Stored
   root-owned 0600 in `/etc/hotline/hotline.env`, never in the repo.
```

- [ ] **Step 4: Full-suite + page smoke**

Run: `cd hotline/server && python -m pytest -q`
Expected: all pass, 0 warnings.

Re-run the Task 5 manual echo-mode check once more (page loads, line-sim flips
state, echo call works).

- [ ] **Step 5: Commit**

```bash
git add hotline/RUNBOOK.md CLAUDE.md \
        docs/superpowers/specs/2026-07-22-pork-phone-offhook-and-earpiece-design.md
git commit -m "docs(hotline): off-hook runbook, surface row, spec v2c amendment"
```

---

## Not in this plan (Paul-gated, after merge)

Deploy per spec §4: push → Pi rsync → env keys from the experiment → restart →
`/healthz` + live page → ATA SNMP config + snmpwalk experiment + T1–T6 physical
matrix. The experiment may return outcome B (no hook OID), in which case the §1.8
auto-dial fallback gets its own follow-up plan; everything in this plan except the
poller (Tasks 1, 2, 6) still ships as-is (state machinery, page, sim endpoint are
detector-agnostic).
