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
