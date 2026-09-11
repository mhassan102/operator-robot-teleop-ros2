"""v1 wire header: 32 bytes, little-endian, payload follows.

offset  size  field
0       4     magic = b'MLNK'
4       1     version = 1
5       1     flags     bit0=heartbeat  bit1=probe  bit2=echo
6       1     traffic_class  0=control  1=media
7       1     path_id
8       4     session_id     u32
12      4     seq            u32
16      8     timestamp_us   u64
24      2     payload_len    u16
26      2     reserved       u16 = 0
28      4     pad            = 0  (header is 32 bytes for the MTU budget)
32–end        payload (payload_len bytes)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = b"MLNK"
VERSION = 1
HEADER_SIZE = 32
MAX_PAYLOAD = 1440  # 1500 - 20 IP - 8 UDP - 32 header

FLAG_HEARTBEAT = 0x01
FLAG_PROBE = 0x02
FLAG_ECHO = 0x04

TC_CONTROL = 0
TC_MEDIA = 1

# magic, ver, flags, tc, path_id, session, seq, ts, plen, reserved, pad
_HEADER = struct.Struct("<4sBBBBIIQHH4s")
assert _HEADER.size == HEADER_SIZE
_PAD = b"\x00\x00\x00\x00"


class HeaderError(ValueError):
    """Packet is not a valid mlink v1 frame."""


class PayloadTooLarge(ValueError):
    """App payload would exceed the 1440-byte MTU budget."""


@dataclass(frozen=True)
class Packet:
    flags: int
    traffic_class: int
    path_id: int
    session_id: int
    seq: int
    timestamp_us: int
    payload: bytes
    version: int = VERSION
    reserved: int = 0

    @property
    def payload_len(self) -> int:
        return len(self.payload)

    @property
    def is_heartbeat(self) -> bool:
        return bool(self.flags & FLAG_HEARTBEAT)

    @property
    def is_probe(self) -> bool:
        return bool(self.flags & FLAG_PROBE)

    @property
    def is_echo(self) -> bool:
        return bool(self.flags & FLAG_ECHO)

    @property
    def is_control_plane(self) -> bool:
        return bool(self.flags & (FLAG_HEARTBEAT | FLAG_PROBE))


def encode(packet: Packet) -> bytes:
    if packet.version != VERSION:
        raise HeaderError(f"unsupported version {packet.version}")
    payload = packet.payload
    if len(payload) > MAX_PAYLOAD:
        raise PayloadTooLarge(
            f"payload {len(payload)} bytes exceeds max {MAX_PAYLOAD}"
        )
    if packet.path_id < 0 or packet.path_id > 255:
        raise HeaderError(f"path_id {packet.path_id} out of range")
    if packet.traffic_class not in (TC_CONTROL, TC_MEDIA):
        raise HeaderError(f"bad traffic_class {packet.traffic_class}")
    header = _HEADER.pack(
        MAGIC,
        packet.version,
        packet.flags & 0xFF,
        packet.traffic_class,
        packet.path_id,
        packet.session_id & 0xFFFFFFFF,
        packet.seq & 0xFFFFFFFF,
        packet.timestamp_us & 0xFFFFFFFFFFFFFFFF,
        len(payload),
        packet.reserved & 0xFFFF,
        _PAD,
    )
    return header + payload


def decode(data: bytes) -> Packet:
    if len(data) < HEADER_SIZE:
        raise HeaderError(f"truncated header ({len(data)} bytes)")
    (
        magic,
        version,
        flags,
        traffic_class,
        path_id,
        session_id,
        seq,
        timestamp_us,
        payload_len,
        reserved,
        _pad,
    ) = _HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        raise HeaderError(f"bad magic {magic!r}")
    if version != VERSION:
        raise HeaderError(f"unsupported version {version}")
    if payload_len > MAX_PAYLOAD:
        raise HeaderError(f"payload_len {payload_len} exceeds max {MAX_PAYLOAD}")
    needed = HEADER_SIZE + payload_len
    if len(data) < needed:
        raise HeaderError(f"truncated payload (have {len(data)}, need {needed})")
    payload = data[HEADER_SIZE:needed]
    return Packet(
        flags=flags,
        traffic_class=traffic_class,
        path_id=path_id,
        session_id=session_id,
        seq=seq,
        timestamp_us=timestamp_us,
        payload=payload,
        version=version,
        reserved=reserved,
    )


def is_data_frame(data: bytes) -> bool:
    """True if `data` looks like an app-payload frame (not heartbeat/probe)."""
    if len(data) < HEADER_SIZE:
        return False
    flags = data[5]
    return (flags & (FLAG_HEARTBEAT | FLAG_PROBE)) == 0
