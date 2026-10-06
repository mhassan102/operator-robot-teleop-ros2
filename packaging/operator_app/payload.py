"""Compact UDP payload for teleop control over mlink.

The operator package ships this copy so the installed app does not
import ``teleop_demo``. The layout matches ``teleop_demo.mlink_payload``
byte for byte, which ``robot_mlink_bridge`` decodes. Not ROS CDR.
Documented little-endian layout, MTU budget 1440 bytes (mlink max
payload). One datagram = one message. Named-pose request and reply
share this mux; there is no second protocol.

Header (4 bytes):
  u8  version = 1
  u8  msg_type
  u16 body_len   # bytes after the header

Types:
  1 COMMAND          operator → robot
  2 HEARTBEAT        operator → robot
  3 ACK              robot → operator
  4 STATE            robot → operator
  5 TOOL_POSE        robot → operator
  6 NAMED_POSE_REQ   operator → robot
  7 NAMED_POSE_REP   robot → operator

Strings: u8 length + UTF-8 bytes (max 255).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Union

VERSION = 1
MAX_PAYLOAD = 1440
HEADER_SIZE = 4

TYPE_COMMAND = 1
TYPE_HEARTBEAT = 2
TYPE_ACK = 3
TYPE_STATE = 4
TYPE_TOOL_POSE = 5
TYPE_NAMED_POSE_REQ = 6
TYPE_NAMED_POSE_REP = 7

_HDR = struct.Struct("<BBH")
_CMD = struct.Struct("<QII7d")
_HB = struct.Struct("<QII")
_ACK = struct.Struct("<QIIII")
_STATE_TWIST = struct.Struct("<7d")
_POSE = struct.Struct("<II7d")
_REQ = struct.Struct("<I")
_REP = struct.Struct("<IB")

assert _HDR.size == HEADER_SIZE


class PayloadError(ValueError):
    """Datagram is not a valid v1 control payload."""


@dataclass(frozen=True)
class Command:
    sequence: int
    stamp_sec: int
    stamp_nsec: int
    lx: float
    ly: float
    lz: float
    ax: float
    ay: float
    az: float
    gripper: float
    frame_id: str
    session_id: str


@dataclass(frozen=True)
class Heartbeat:
    sequence: int
    stamp_sec: int
    stamp_nsec: int
    session_id: str


@dataclass(frozen=True)
class Ack:
    sequence: int
    source_sec: int
    source_nsec: int
    recv_sec: int
    recv_nsec: int
    session_id: str
    disposition: str


@dataclass(frozen=True)
class State:
    stamp_sec: int
    stamp_nsec: int
    session_id: str
    frame_id: str
    connection_state: str
    watchdog_state: str
    last_disposition: str
    lx: float
    ly: float
    lz: float
    ax: float
    ay: float
    az: float
    gripper: float


@dataclass(frozen=True)
class ToolPose:
    stamp_sec: int
    stamp_nsec: int
    x: float
    y: float
    z: float
    qx: float
    qy: float
    qz: float
    qw: float


@dataclass(frozen=True)
class NamedPoseReq:
    req_id: int
    name: str


@dataclass(frozen=True)
class NamedPoseRep:
    req_id: int
    success: bool
    message: str


Decoded = Union[Command, Heartbeat, Ack, State, ToolPose, NamedPoseReq, NamedPoseRep]


def encode_command(msg: Command) -> bytes:
    body = bytearray(_CMD.pack(
        msg.sequence,
        msg.stamp_sec,
        msg.stamp_nsec,
        msg.lx,
        msg.ly,
        msg.lz,
        msg.ax,
        msg.ay,
        msg.az,
        msg.gripper,
    ))
    _put_str(body, msg.frame_id)
    _put_str(body, msg.session_id)
    return _frame(TYPE_COMMAND, bytes(body))


def encode_heartbeat(msg: Heartbeat) -> bytes:
    body = bytearray(_HB.pack(msg.sequence, msg.stamp_sec, msg.stamp_nsec))
    _put_str(body, msg.session_id)
    return _frame(TYPE_HEARTBEAT, bytes(body))


def encode_ack(msg: Ack) -> bytes:
    body = bytearray(
        _ACK.pack(
            msg.sequence,
            msg.source_sec,
            msg.source_nsec,
            msg.recv_sec,
            msg.recv_nsec,
        )
    )
    _put_str(body, msg.session_id)
    _put_str(body, msg.disposition)
    return _frame(TYPE_ACK, bytes(body))


def encode_state(msg: State) -> bytes:
    body = bytearray()
    body.extend(struct.pack("<II", msg.stamp_sec, msg.stamp_nsec))
    _put_str(body, msg.session_id)
    _put_str(body, msg.frame_id)
    _put_str(body, msg.connection_state)
    _put_str(body, msg.watchdog_state)
    _put_str(body, msg.last_disposition)
    body.extend(
        _STATE_TWIST.pack(
            msg.lx, msg.ly, msg.lz, msg.ax, msg.ay, msg.az, msg.gripper
        )
    )
    return _frame(TYPE_STATE, bytes(body))


def encode_tool_pose(msg: ToolPose) -> bytes:
    body = _POSE.pack(
        msg.stamp_sec,
        msg.stamp_nsec,
        msg.x,
        msg.y,
        msg.z,
        msg.qx,
        msg.qy,
        msg.qz,
        msg.qw,
    )
    return _frame(TYPE_TOOL_POSE, body)


def encode_named_pose_req(msg: NamedPoseReq) -> bytes:
    body = bytearray(_REQ.pack(msg.req_id))
    _put_str(body, msg.name)
    return _frame(TYPE_NAMED_POSE_REQ, bytes(body))


def encode_named_pose_rep(msg: NamedPoseRep) -> bytes:
    body = bytearray(_REP.pack(msg.req_id, 1 if msg.success else 0))
    _put_str(body, msg.message)
    return _frame(TYPE_NAMED_POSE_REP, bytes(body))


def decode(data: bytes) -> tuple[int, Decoded]:
    if len(data) < HEADER_SIZE:
        raise PayloadError(f"truncated header ({len(data)} bytes)")
    version, msg_type, body_len = _HDR.unpack_from(data, 0)
    if version != VERSION:
        raise PayloadError(f"unsupported version {version}")
    needed = HEADER_SIZE + body_len
    if len(data) < needed:
        raise PayloadError(f"truncated body (have {len(data)}, need {needed})")
    if needed > MAX_PAYLOAD:
        raise PayloadError(f"payload {needed} exceeds max {MAX_PAYLOAD}")
    body = data[HEADER_SIZE:needed]
    if msg_type == TYPE_COMMAND:
        return msg_type, _decode_command(body)
    if msg_type == TYPE_HEARTBEAT:
        return msg_type, _decode_heartbeat(body)
    if msg_type == TYPE_ACK:
        return msg_type, _decode_ack(body)
    if msg_type == TYPE_STATE:
        return msg_type, _decode_state(body)
    if msg_type == TYPE_TOOL_POSE:
        return msg_type, _decode_tool_pose(body)
    if msg_type == TYPE_NAMED_POSE_REQ:
        return msg_type, _decode_named_pose_req(body)
    if msg_type == TYPE_NAMED_POSE_REP:
        return msg_type, _decode_named_pose_rep(body)
    raise PayloadError(f"unknown msg_type {msg_type}")


def _frame(msg_type: int, body: bytes) -> bytes:
    total = HEADER_SIZE + len(body)
    if total > MAX_PAYLOAD:
        raise PayloadError(f"payload {total} exceeds max {MAX_PAYLOAD}")
    return _HDR.pack(VERSION, msg_type, len(body)) + body


def _put_str(buf: bytearray, text: str) -> None:
    raw = text.encode("utf-8")
    if len(raw) > 255:
        raise PayloadError("string longer than 255 bytes")
    buf.append(len(raw))
    buf.extend(raw)


def _get_str(data: bytes, offset: int) -> tuple[str, int]:
    if offset >= len(data):
        raise PayloadError("truncated string length")
    n = data[offset]
    start = offset + 1
    end = start + n
    if end > len(data):
        raise PayloadError("truncated string")
    try:
        text = data[start:end].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PayloadError("string is not utf-8") from exc
    return text, end


def _decode_command(body: bytes) -> Command:
    if len(body) < _CMD.size + 2:
        raise PayloadError("truncated command")
    fields = _CMD.unpack_from(body, 0)
    offset = _CMD.size
    frame_id, offset = _get_str(body, offset)
    session_id, offset = _get_str(body, offset)
    return Command(
        sequence=fields[0],
        stamp_sec=fields[1],
        stamp_nsec=fields[2],
        lx=fields[3],
        ly=fields[4],
        lz=fields[5],
        ax=fields[6],
        ay=fields[7],
        az=fields[8],
        gripper=fields[9],
        frame_id=frame_id,
        session_id=session_id,
    )


def _decode_heartbeat(body: bytes) -> Heartbeat:
    if len(body) < _HB.size + 1:
        raise PayloadError("truncated heartbeat")
    sequence, stamp_sec, stamp_nsec = _HB.unpack_from(body, 0)
    session_id, _offset = _get_str(body, _HB.size)
    return Heartbeat(sequence, stamp_sec, stamp_nsec, session_id)


def _decode_ack(body: bytes) -> Ack:
    if len(body) < _ACK.size + 2:
        raise PayloadError("truncated ack")
    sequence, source_sec, source_nsec, recv_sec, recv_nsec = _ACK.unpack_from(
        body, 0
    )
    offset = _ACK.size
    session_id, offset = _get_str(body, offset)
    disposition, offset = _get_str(body, offset)
    return Ack(
        sequence,
        source_sec,
        source_nsec,
        recv_sec,
        recv_nsec,
        session_id,
        disposition,
    )


def _decode_state(body: bytes) -> State:
    if len(body) < 8 + 5:
        raise PayloadError("truncated state")
    stamp_sec, stamp_nsec = struct.unpack_from("<II", body, 0)
    offset = 8
    session_id, offset = _get_str(body, offset)
    frame_id, offset = _get_str(body, offset)
    connection_state, offset = _get_str(body, offset)
    watchdog_state, offset = _get_str(body, offset)
    last_disposition, offset = _get_str(body, offset)
    if offset + _STATE_TWIST.size > len(body):
        raise PayloadError("truncated state twist")
    twist = _STATE_TWIST.unpack_from(body, offset)
    return State(
        stamp_sec=stamp_sec,
        stamp_nsec=stamp_nsec,
        session_id=session_id,
        frame_id=frame_id,
        connection_state=connection_state,
        watchdog_state=watchdog_state,
        last_disposition=last_disposition,
        lx=twist[0],
        ly=twist[1],
        lz=twist[2],
        ax=twist[3],
        ay=twist[4],
        az=twist[5],
        gripper=twist[6],
    )


def _decode_tool_pose(body: bytes) -> ToolPose:
    if len(body) < _POSE.size:
        raise PayloadError("truncated tool pose")
    fields = _POSE.unpack_from(body, 0)
    return ToolPose(*fields)


def _decode_named_pose_req(body: bytes) -> NamedPoseReq:
    if len(body) < _REQ.size + 1:
        raise PayloadError("truncated named pose req")
    (req_id,) = _REQ.unpack_from(body, 0)
    name, _offset = _get_str(body, _REQ.size)
    return NamedPoseReq(req_id, name)


def _decode_named_pose_rep(body: bytes) -> NamedPoseRep:
    if len(body) < _REP.size + 1:
        raise PayloadError("truncated named pose rep")
    req_id, success = _REP.unpack_from(body, 0)
    message, _offset = _get_str(body, _REP.size)
    return NamedPoseRep(req_id, bool(success), message)
