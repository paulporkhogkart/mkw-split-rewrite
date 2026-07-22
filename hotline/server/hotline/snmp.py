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
    try:
        arcs = [int(a) for a in dotted.strip(".").split(".") if a != ""]
    except ValueError:
        raise SnmpError("bad oid")
    if len(arcs) < 2:
        raise SnmpError("oid too short")
    if any(arc < 0 for arc in arcs):
        raise SnmpError("bad oid")
    first = 40 * arcs[0] + arcs[1]
    if not (arcs[0] <= 2 and arcs[1] <= 39 and first <= 255):
        raise SnmpError("bad oid")
    body = bytearray([first])
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
