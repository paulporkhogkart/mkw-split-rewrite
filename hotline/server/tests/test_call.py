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
