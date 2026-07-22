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
