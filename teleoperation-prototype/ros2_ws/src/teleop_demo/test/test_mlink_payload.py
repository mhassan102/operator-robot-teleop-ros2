"""Compact mlink control payload: no ROS, no sockets."""

from __future__ import annotations

import pytest

from teleop_demo.mlink_payload import (
    MAX_PAYLOAD,
    TYPE_ACK,
    TYPE_COMMAND,
    TYPE_HEARTBEAT,
    TYPE_NAMED_POSE_REP,
    TYPE_NAMED_POSE_REQ,
    TYPE_STATE,
    TYPE_TOOL_POSE,
    Ack,
    Command,
    Heartbeat,
    NamedPoseRep,
    NamedPoseReq,
    PayloadError,
    State,
    ToolPose,
    decode,
    encode_ack,
    encode_command,
    encode_heartbeat,
    encode_named_pose_rep,
    encode_named_pose_req,
    encode_state,
    encode_tool_pose,
)


def test_command_roundtrip() -> None:
    msg = Command(
        sequence=7,
        stamp_sec=1,
        stamp_nsec=2,
        lx=0.05,
        ly=0.0,
        lz=0.0,
        ax=0.0,
        ay=0.0,
        az=0.2,
        gripper=1.0,
        frame_id="tool0",
        session_id="abc123def456",
    )
    data = encode_command(msg)
    assert len(data) <= MAX_PAYLOAD
    kind, got = decode(data)
    assert kind == TYPE_COMMAND
    assert got == msg


def test_heartbeat_roundtrip() -> None:
    msg = Heartbeat(3, 4, 5, "sess")
    kind, got = decode(encode_heartbeat(msg))
    assert kind == TYPE_HEARTBEAT
    assert got == msg


def test_ack_state_pose_named_roundtrip() -> None:
    ack = Ack(9, 1, 2, 3, 4, "s", "ok")
    kind, got = decode(encode_ack(ack))
    assert kind == TYPE_ACK
    assert got == ack

    state = State(
        1,
        2,
        "s",
        "tool0",
        "CONNECTED",
        "OK",
        "accepted",
        0.01,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    )
    kind, got = decode(encode_state(state))
    assert kind == TYPE_STATE
    assert got == state

    pose = ToolPose(1, 2, 0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 1.0)
    kind, got = decode(encode_tool_pose(pose))
    assert kind == TYPE_TOOL_POSE
    assert got == pose

    req = NamedPoseReq(11, "fold")
    kind, got = decode(encode_named_pose_req(req))
    assert kind == TYPE_NAMED_POSE_REQ
    assert got == req

    rep = NamedPoseRep(11, True, "at fold")
    kind, got = decode(encode_named_pose_rep(rep))
    assert kind == TYPE_NAMED_POSE_REP
    assert got == rep


def test_rejects_truncated_and_bad_version() -> None:
    with pytest.raises(PayloadError, match="truncated"):
        decode(b"ML")
    good = encode_heartbeat(Heartbeat(1, 0, 0, "x"))
    bad = bytes([2]) + good[1:]
    with pytest.raises(PayloadError, match="version"):
        decode(bad)


def test_command_fits_mtu() -> None:
    msg = Command(
        sequence=1,
        stamp_sec=0,
        stamp_nsec=0,
        lx=0.0,
        ly=0.0,
        lz=0.0,
        ax=0.0,
        ay=0.0,
        az=0.0,
        gripper=0.0,
        frame_id="tool0",
        session_id="x" * 12,
    )
    assert len(encode_command(msg)) < 200
