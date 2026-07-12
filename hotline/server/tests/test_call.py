from __future__ import annotations

import asyncio

import numpy as np

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


async def test_pump_exception_ends_call_cleanly(tmp_path):
    bus, phone, sess, _ = make_session(tmp_path, seconds=30)
    await bus.start()
    rt = bus.subscribe("rt")

    async def broken_send(frame: bytes) -> None:
        raise RuntimeError("transport died")

    phone.send_frame = broken_send  # type: ignore[method-assign]
    await sess.start()
    sess.on_phone_answered()
    await asyncio.wait_for(sess.done.wait(), 2)
    assert sess.outcome == "dropped"
    types = await drain(rt)
    assert types[-1] == "call_ended"
    assert (tmp_path / "c1" / "mix.wav").exists()  # recorder closed, not zombied
    await bus.stop()


async def test_caller_recovered_cancels_grace(tmp_path):
    bus, phone, sess, _ = make_session(tmp_path, seconds=30, grace_s=0.1)
    await bus.start()
    await sess.start()
    sess.on_phone_answered()
    sess.on_caller_lost()
    sess.on_caller_recovered()
    await asyncio.sleep(0.3)  # well past grace_s
    assert not sess.done.is_set()  # call survived the blip
    await sess.end("test")
    await bus.stop()


async def test_caller_tones_mixed_not_doubled(tmp_path):
    bus, phone, sess, to_caller = make_session(tmp_path, seconds=0.5)
    await bus.start()
    await sess.start()
    sess.on_phone_answered()  # warning fires on the first tick (seconds < 10)
    fed = 0
    for _ in range(15):  # phone streams silence at real cadence during the beep
        sess.on_phone_frame(audio.SILENCE_FRAME)
        fed += 1
        await asyncio.sleep(0.02)
    # no frame-doubling: caller got at most the phone frames plus a few fallbacks
    assert len(to_caller) <= fed + 3
    # and the beep actually reached the caller, mixed into those frames
    loudest = max(
        int(np.abs(np.frombuffer(f, dtype=np.int16)).max()) for f in to_caller)
    assert loudest > 5000
    await sess.end("test")
    await bus.stop()


async def test_recovered_during_end_does_not_zombie(tmp_path):
    bus, phone, sess, _ = make_session(tmp_path, seconds=30, grace_s=0.05)
    await bus.start()
    release = asyncio.Event()

    async def slow_hangup() -> None:
        await release.wait()

    phone.hangup = slow_hangup  # type: ignore[method-assign]
    await sess.start()
    sess.on_phone_answered()
    sess.on_caller_lost()
    await asyncio.sleep(0.15)   # grace fired; end() is suspended inside hangup
    sess.on_caller_recovered()  # must NOT cancel the ending grace task
    release.set()
    await asyncio.wait_for(sess.done.wait(), 2)
    assert sess.outcome == "dropped"
    await bus.stop()
