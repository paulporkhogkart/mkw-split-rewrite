from __future__ import annotations

import asyncio
import re
import uuid

import aiohttp
import pytest

from hotline.__main__ import AriPhoneLeg, build_and_run
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


class FakeAri:
    def __init__(self, fail_bridge: bool = False) -> None:
        self.listeners: list = []
        self.hungup: list[str] = []
        self.fail_bridge = fail_bridge
        self.bridged = None
        self.em_uuid = None

    def on_event(self, cb) -> None:
        self.listeners.append(cb)

    def off_event(self, cb) -> None:
        if cb in self.listeners:
            self.listeners.remove(cb)

    async def originate_phone(self, caller_id: str, u: str,
                              timeout_s: int = 30) -> str:
        return "ch-7"

    async def external_media(self, u: str, host: str) -> str:
        self.em_uuid = u
        return "ch-em"

    async def bridge(self, ids: list[str]) -> str:
        if self.fail_bridge:
            raise RuntimeError("bridge failed")
        self.bridged = ids
        return "br"

    async def hangup(self, ch: str) -> None:
        self.hungup.append(ch)


class FakeSession:
    def __init__(self) -> None:
        self.call_id = str(uuid.uuid4())
        self.answered = False
        self.hungup = False

    def on_phone_answered(self) -> None:
        self.answered = True

    def on_phone_hungup(self) -> None:
        self.hungup = True


async def test_ari_leg_happy_answer():
    ari = FakeAri()
    sess = FakeSession()
    leg = AriPhoneLeg(sess, ari, 9101)
    await leg.ring("PORK")
    ari.listeners[0]({"type": "StasisStart", "channel": {"id": "ch-7"}})
    await asyncio.sleep(0.05)
    assert sess.answered and ari.bridged == ["ch-7", "ch-em"]
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        ari.em_uuid)  # Asterisk res_audiosocket parses with libuuid: dashed form only


async def test_ari_leg_bridge_failure_frees_call():
    ari = FakeAri(fail_bridge=True)
    sess = FakeSession()
    leg = AriPhoneLeg(sess, ari, 9101)
    await leg.ring("PORK")
    ari.listeners[0]({"type": "StasisStart", "channel": {"id": "ch-7"}})
    await asyncio.sleep(0.05)
    assert not sess.answered
    assert sess.hungup            # call slot freed
    assert "ch-7" in ari.hungup   # phone channel not left dangling
    assert ari.listeners == []    # leg disposed/unsubscribed


async def test_ari_leg_hangup_disposes_listener():
    ari = FakeAri()
    sess = FakeSession()
    leg = AriPhoneLeg(sess, ari, 9101)
    await leg.ring("PORK")
    await leg.hangup()
    assert sess.hungup            # PhoneLeg contract: hangup() ends the call
    assert ari.listeners == []


async def test_ari_leg_ring_failure_disposes():
    class FailingAri(FakeAri):
        async def originate_phone(self, caller_id: str, u: str,
                                  timeout_s: int = 30) -> str:
            raise RuntimeError("asterisk down")

    ari = FailingAri()
    sess = FakeSession()
    leg = AriPhoneLeg(sess, ari, 9101)
    with pytest.raises(RuntimeError):
        await leg.ring("PORK")
    assert ari.listeners == []


async def test_ari_leg_replays_events_stashed_during_originate():
    ari = FakeAri()
    sess = FakeSession()
    leg = AriPhoneLeg(sess, ari, 9101)
    # ChannelDestroyed arrives BEFORE originate's HTTP response assigns the id
    leg._on_ari_event({"type": "ChannelDestroyed", "channel": {"id": "ch-7"}})
    await leg.ring("PORK")   # originate returns ch-7; stash replays
    assert sess.hungup       # offline-ATA fast-destroy ends the call, no wedge
    assert ari.listeners == []


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
