from proto.header import (
    FLAG_ECHO,
    FLAG_HEARTBEAT,
    FLAG_PROBE,
    HEADER_SIZE,
    MAGIC,
    MAX_PAYLOAD,
    TC_CONTROL,
    TC_MEDIA,
    VERSION,
    HeaderError,
    Packet,
    PayloadTooLarge,
    decode,
    encode,
)


def test_header_is_32_bytes() -> None:
    pkt = Packet(
        flags=0,
        traffic_class=TC_CONTROL,
        path_id=1,
        session_id=7,
        seq=42,
        timestamp_us=123456,
        payload=b"hi",
    )
    raw = encode(pkt)
    assert raw[:4] == MAGIC
    assert raw[4] == VERSION
    assert len(raw) == HEADER_SIZE + 2
    assert decode(raw) == pkt


def test_roundtrip_flags_and_classes() -> None:
    pkt = Packet(
        flags=FLAG_HEARTBEAT | FLAG_PROBE | FLAG_ECHO,
        traffic_class=TC_MEDIA,
        path_id=255,
        session_id=1,
        seq=0,
        timestamp_us=99,
        payload=b"",
    )
    assert decode(encode(pkt)) == pkt


def test_rejects_bad_magic() -> None:
    pkt = Packet(
        flags=0,
        traffic_class=TC_CONTROL,
        path_id=0,
        session_id=1,
        seq=1,
        timestamp_us=0,
        payload=b"x",
    )
    raw = bytearray(encode(pkt))
    raw[0:4] = b"XXXX"
    try:
        decode(bytes(raw))
        assert False, "expected HeaderError"
    except HeaderError as exc:
        assert "magic" in str(exc).lower()


def test_rejects_truncated() -> None:
    try:
        decode(b"MLNK")
        assert False, "expected HeaderError"
    except HeaderError:
        pass


def test_payload_too_large_on_encode() -> None:
    pkt = Packet(
        flags=0,
        traffic_class=TC_MEDIA,
        path_id=0,
        session_id=1,
        seq=1,
        timestamp_us=0,
        payload=b"x" * (MAX_PAYLOAD + 1),
    )
    try:
        encode(pkt)
        assert False, "expected PayloadTooLarge"
    except PayloadTooLarge:
        pass


def test_max_payload_ok() -> None:
    pkt = Packet(
        flags=0,
        traffic_class=TC_MEDIA,
        path_id=0,
        session_id=1,
        seq=1,
        timestamp_us=0,
        payload=b"y" * MAX_PAYLOAD,
    )
    assert decode(encode(pkt)).payload == pkt.payload
