from __future__ import annotations

import asyncio
import uuid as uuidlib

import aiohttp
import numpy as np
import pytest
from aiohttp.test_utils import TestClient, TestServer

from hotline import audio, audiosocket as aus
from hotline.call import CallSession
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
            self._send_audio = None

        async def ring(self, caller_name: str) -> None:
            # "Paul lifts the horn": connect AudioSocket with the call uuid
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", cfg.audiosocket_port)
            self._writer = writer
            u = uuidlib.UUID(self._session.call_id)
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
                # 25 frames: the pump's first ~5 are jitter pre-buffer silence
                # (target_ms=100 -> 5 frames; plus a few dropped during the
                # set_audio_sender handshake), then the 20-frame caller tone
                # -- 25 guarantees tone frames land inside the window.
                while len(phone_got) < 25:
                    kind, payload = await aus.read_frame(reader)
                    if kind == aus.KIND_AUDIO:
                        phone_got.append(payload)

            await asyncio.gather(speak(), listen())
            fake_done.set()

        async def hangup(self) -> None:
            if self._writer is not None:
                self._writer.close()  # "Paul hangs up": close our AudioSocket leg
            self._session.on_phone_hungup()  # PhoneLeg contract: hangup() ends the call

        def set_audio_sender(self, cb) -> None:
            self._send_audio = cb

        async def send_frame(self, frame: bytes) -> None:
            # audio to the phone flows via the AudioSocket session; the
            # controller wires this in _on_audiosocket_session, exactly how
            # AriPhoneLeg gets wired via set_audio_sender.
            if self._send_audio:
                await self._send_audio(frame)

    ctl = Controller(cfg, bus, db, phone_leg_factory=FakeAtaPhoneLeg)
    # force real factory even though env token says dev
    ctl.cfg = cfg
    await ctl.start()
    client = TestClient(TestServer(make_app(cfg, ctl)))
    await client.start_server()
    rt = bus.subscribe("rt")

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
        while len(caller_got) < 10 * audio.FRAME_BYTES:
            msg = await asyncio.wait_for(ws.receive(), 5)
            if msg.type == aiohttp.WSMsgType.BINARY:
                caller_got.extend(msg.data)

    await asyncio.gather(send_caller(), recv_caller())

    # phone->caller direction arrived at the caller WS -- and carried the
    # 220 Hz tone's energy, not just live-but-silent plumbing
    assert len(caller_got) >= 10 * audio.FRAME_BYTES
    caller_pcm = np.frombuffer(bytes(caller_got), dtype=np.int16)
    assert np.abs(caller_pcm).max() > 5000  # phone tone actually reached the caller

    # caller->phone direction arrived at the fake Asterisk client, via the
    # controller's set_audio_sender wiring -- and carried the 440 Hz tone
    await asyncio.wait_for(fake_done.wait(), 5)
    assert len(phone_got) >= 25
    phone_pcm = np.frombuffer(b"".join(phone_got), dtype=np.int16)
    assert np.abs(phone_pcm).max() > 5000  # caller tone actually crossed the pipe

    await client.post("/admin/hangup?token=dev-token")
    await asyncio.sleep(0.2)

    # events observed, in order: ringing and active seen, ended is last
    seen = []
    while not rt.empty():
        seen.append((await rt.get())["type"])
    assert "call_ringing" in seen and "call_active" in seen
    assert seen[-1] == "call_ended"
    bus.unsubscribe("rt", rt)

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


async def test_ring_unwedges_slot_when_start_raises(tmp_path, unused_tcp_port, monkeypatch):
    """Echo-mode session with CallSession.start monkeypatched to raise --
    exercises test_ring's unwind path: on a start() failure it must free
    self._call / self._call_phone_leg / self._reap_task before re-raising,
    or the single call slot wedges and every subsequent test_ring() 409s
    with "call already active". A real leg's ring() failure (e.g. Asterisk
    down) propagates through start() and takes this same path."""
    cfg = Config.from_env({
        "HOTLINE_ENV": "dev", "HOTLINE_DATA_DIR": str(tmp_path),
        "HOTLINE_AUDIOSOCKET_PORT": str(unused_tcp_port),
        "HOTLINE_ECHO": "1",
    })
    bus = EventBus(delay_n=0.05); await bus.start()
    db = Db(tmp_path / "h.db"); db.init()
    ctl = Controller(cfg, bus, db, phone_leg_factory=None)
    await ctl.start()
    client = TestClient(TestServer(make_app(cfg, ctl)))
    await client.start_server()
    ws = await client.ws_connect("/ws/audio?token=dev-token")

    real_start = CallSession.start
    attempts = {"n": 0}

    async def flaky_start(self):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("asterisk down")
        await real_start(self)

    monkeypatch.setattr(CallSession, "start", flaky_start)

    with pytest.raises(RuntimeError, match="asterisk down"):
        await ctl.test_ring(30)
    assert ctl._call is None             # slot freed, not wedged...
    assert ctl._call_phone_leg is None   # ...including the leg reference
    # let the first attempt's already-spawned _reap task finish its DB write
    # before firing a second call over the same sqlite connection
    await asyncio.sleep(0.05)

    call_id = await ctl.test_ring(30)    # second attempt succeeds: unwedged
    assert call_id

    await ctl.hangup_active()
    await asyncio.sleep(0.1)
    row = db._conn.execute(
        "SELECT outcome FROM calls WHERE call_id=?", (call_id,)).fetchone()
    assert row and row[0] == "completed"

    await ws.close(); await client.close()
    await ctl.stop(); await bus.stop(); db.close()
