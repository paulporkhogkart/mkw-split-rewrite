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


async def test_on_session_exception_fails_closed(unused_tcp_port):
    async def on_session(_sess: aus.AudioSocketSession) -> bool:
        raise RuntimeError("db down")

    server = aus.AudioSocketServer(unused_tcp_port, on_session)
    await server.start()
    reader, writer = await _connect(unused_tcp_port, uuid.uuid4())
    kind, _ = await aus.read_frame(reader)
    assert kind == aus.KIND_TERMINATE
    writer.close()
    await server.stop()


async def test_stop_completes_session_cleanup(unused_tcp_port):
    closed = asyncio.Event()

    async def on_session(sess: aus.AudioSocketSession) -> bool:
        sess.on_closed(closed.set)
        return True

    server = aus.AudioSocketServer(unused_tcp_port, on_session)
    await server.start()
    _reader, writer = await _connect(unused_tcp_port, uuid.uuid4())
    await asyncio.sleep(0.05)  # session task is running
    await server.stop()
    assert closed.is_set()  # cleanup finished BEFORE stop returned
    writer.close()


async def test_send_audio_survives_peer_disconnect(unused_tcp_port):
    sessions: list[aus.AudioSocketSession] = []

    async def on_session(sess: aus.AudioSocketSession) -> bool:
        sessions.append(sess)
        return True

    server = aus.AudioSocketServer(unused_tcp_port, on_session)
    await server.start()
    _reader, writer = await _connect(unused_tcp_port, uuid.uuid4())
    await asyncio.sleep(0.05)
    sess = sessions[0]

    async def dead_drain() -> None:
        raise ConnectionResetError("peer gone")

    sess._writer.drain = dead_drain  # deterministic mid-call hangup
    await sess.send_audio(SILENCE_FRAME)  # exercises the except-branch: no raise
    assert sess._closed
    await sess.send_audio(SILENCE_FRAME)  # guard branch: still no raise
    writer.close()
    await server.stop()


async def test_stalled_on_session_terminated(unused_tcp_port):
    async def on_session(_sess: aus.AudioSocketSession) -> bool:
        await asyncio.sleep(10)
        return True

    server = aus.AudioSocketServer(unused_tcp_port, on_session,
                                   on_session_timeout=0.05)
    await server.start()
    reader, writer = await _connect(unused_tcp_port, uuid.uuid4())
    kind, _ = await aus.read_frame(reader)
    assert kind == aus.KIND_TERMINATE
    writer.close()
    await server.stop()
