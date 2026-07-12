from __future__ import annotations

import asyncio
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

    def on_event(self, cb) -> None:
        self.listeners.append(cb)

    def off_event(self, cb) -> None:
        if cb in self.listeners:
            self.listeners.remove(cb)

    async def originate_phone(self, caller_id: str, u: str) -> str:
        return "ch-7"

    async def external_media(self, u: str, host: str) -> str:
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
        self.call_id = uuid.uuid4().hex
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
        async def originate_phone(self, caller_id: str, u: str) -> str:
            raise RuntimeError("asterisk down")

    ari = FailingAri()
    sess = FakeSession()
    leg = AriPhoneLeg(sess, ari, 9101)
    with pytest.raises(RuntimeError):
        await leg.ring("PORK")
    assert ari.listeners == []
