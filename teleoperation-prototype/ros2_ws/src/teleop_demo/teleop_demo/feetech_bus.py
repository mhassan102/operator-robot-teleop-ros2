"""Exclusive Feetech bus: one owner of the serial port, gripper ID only.

Wrong or missing serial fails clearly. Never falls back to Gazebo.
"""

from __future__ import annotations

import errno
import fcntl
import glob
import os
import select
import termios
import time
from typing import Protocol

from teleop_demo.feetech_protocol import (
    ADDR_GOAL_POSITION,
    ADDR_PRESENT_POSITION,
    ADDR_TORQUE_ENABLE,
    ARM_JOINT_IDS,
    BROADCAST_ID,
    ForbiddenServoCommand,
    INST_PING,
    INST_READ,
    INST_WRITE,
    POSITION_MAX,
    POSITION_MIN,
    assert_command_allowed,
    decode_position,
    encode_u16_le,
    parse_instruction,
    ping_packet,
    read_packet,
    status_packet,
    write_packet,
)

TIOCEXCL = getattr(termios, "TIOCEXCL", 0x540C)
TIOCNXCL = getattr(termios, "TIOCNXCL", 0x540D)


class FeetechBusError(RuntimeError):
    """Serial or protocol failure talking to the gripper."""


class SerialMissingError(FeetechBusError):
    """Port does not exist. Do not start Gazebo."""


class SerialBusyError(FeetechBusError):
    """Another process already owns the port (for example LeRobot)."""


class Transport(Protocol):
    def transfer(self, packet: bytes) -> bytes:
        ...

    def close(self) -> None:
        ...


def _gazebo_refused(detail: str) -> str:
    return f"{detail}; refusing to fall back to Gazebo"


def port_holder_pids(port: str, exclude_pid: int | None = None) -> list[int]:
    """Return process ids that already have `port` open."""
    try:
        target = os.stat(port)
    except OSError:
        return []
    holders: list[int] = []
    seen: set[int] = set()
    for fd_path in glob.glob("/proc/[0-9]*/fd/*"):
        try:
            pid = int(fd_path.split("/")[2])
        except (IndexError, ValueError):
            continue
        if exclude_pid is not None and pid == exclude_pid:
            continue
        if pid in seen:
            continue
        try:
            opened = os.stat(fd_path)
        except OSError:
            continue
        if opened.st_dev == target.st_dev and opened.st_ino == target.st_ino:
            seen.add(pid)
            holders.append(pid)
    return holders


class SerialTransport:
    """Raw 8N1 serial with flock + TIOCEXCL. One process owns the port."""

    def __init__(self, fd: int, port: str, timeout_s: float = 0.05) -> None:
        self.fd = fd
        self.port = port
        self.timeout_s = timeout_s

    @classmethod
    def open_exclusive(
        cls,
        port: str,
        baudrate: int = 1_000_000,
        timeout_s: float = 0.05,
    ) -> "SerialTransport":
        if not os.path.exists(port):
            raise SerialMissingError(_gazebo_refused(f"serial port missing: {port}"))
        holders = port_holder_pids(port, exclude_pid=os.getpid())
        if holders:
            raise SerialBusyError(
                _gazebo_refused(
                    f"{port} is busy (pids {holders}; another process owns it, e.g. LeRobot)"
                )
            )
        try:
            fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ENODEV, errno.ENXIO):
                raise SerialMissingError(
                    _gazebo_refused(f"serial port missing: {port} ({exc})")
                ) from exc
            if exc.errno in (errno.EBUSY, errno.EACCES, errno.EPERM):
                raise SerialBusyError(
                    _gazebo_refused(f"{port} is busy or inaccessible ({exc})")
                ) from exc
            raise FeetechBusError(_gazebo_refused(f"could not open {port}: {exc}")) from exc
        try:
            _lock_exclusive(fd, port)
            _configure_tty(fd, baudrate)
        except Exception:
            os.close(fd)
            raise
        return cls(fd, port, timeout_s=timeout_s)

    def transfer(self, packet: bytes) -> bytes:
        termios.tcflush(self.fd, termios.TCIFLUSH)
        os.write(self.fd, packet)
        deadline = time.monotonic() + self.timeout_s
        buf = bytearray()
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            ready, _, _ = select.select([self.fd], [], [], max(0.0, remaining))
            if not ready:
                break
            try:
                chunk = os.read(self.fd, 128)
            except BlockingIOError:
                chunk = b""
            if not chunk:
                continue
            buf.extend(chunk)
            complete = _first_complete_packet(bytes(buf))
            if complete is not None:
                return complete
        raise FeetechBusError(
            _gazebo_refused(
                f"no Feetech status on {self.port} (got {len(buf)} bytes)"
            )
        )

    def close(self) -> None:
        fd = self.fd
        if fd < 0:
            return
        self.fd = -1
        try:
            fcntl.ioctl(fd, TIOCNXCL)
        except OSError:
            pass
        os.close(fd)


def _lock_exclusive(fd: int, port: str) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise SerialBusyError(
            _gazebo_refused(f"{port} is busy (flock failed: {exc})")
        ) from exc
    try:
        fcntl.ioctl(fd, TIOCEXCL)
    except OSError as exc:
        if exc.errno == errno.EBUSY:
            raise SerialBusyError(
                _gazebo_refused(f"{port} is busy (TIOCEXCL: {exc})")
            ) from exc


def _configure_tty(fd: int, baudrate: int) -> None:
    baud_const = {
        9600: termios.B9600,
        115200: termios.B115200,
        1_000_000: getattr(termios, "B1000000", None),
    }.get(int(baudrate))
    if baud_const is None:
        raise FeetechBusError(_gazebo_refused(f"unsupported baudrate {baudrate}"))
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
    attrs[3] = 0
    attrs[4] = baud_const
    attrs[5] = baud_const
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)


def _first_complete_packet(buf: bytes) -> bytes | None:
    start = buf.find(b"\xff\xff")
    if start < 0 or len(buf) - start < 6:
        return None
    length = buf[start + 3]
    need = start + length + 4
    if len(buf) < need:
        return None
    return buf[start:need]


class FakeTransport:
    """In-memory STS bus for tests. No hardware, no serial."""

    def __init__(
        self,
        present: dict[int, int] | None = None,
        model_number: int = 777,
    ) -> None:
        default = {1: 2106, 2: 862, 3: 3072, 4: 2761, 5: 1062, 6: 2061}
        self.present = dict(present) if present is not None else default
        self.torque = {servo_id: 0 for servo_id in self.present}
        self.goal = dict(self.present)
        self.model_number = model_number
        self.packets: list[bytes] = []
        self.writes: list[dict[str, object]] = []
        self.closed = False

    def transfer(self, packet: bytes) -> bytes:
        self.packets.append(packet)
        parsed = parse_instruction(packet)
        servo_id = int(parsed["id"])
        instruction = int(parsed["instruction"])
        address = parsed["address"]
        data = bytes(parsed["data"])
        if instruction == INST_PING:
            if servo_id not in self.present:
                raise FeetechBusError(f"no servo id {servo_id} on fake bus")
            return status_packet(servo_id)
        if instruction == INST_READ:
            payload = self._read(servo_id, int(address or 0), data[0] if data else 0)
            return status_packet(servo_id, data=payload)
        if instruction == INST_WRITE:
            self._write(servo_id, int(address or 0), data)
            return status_packet(servo_id)
        raise ForbiddenServoCommand(f"fake bus refused instruction {instruction:#x}")

    def _read(self, servo_id: int, address: int, length: int) -> bytes:
        if servo_id not in self.present:
            raise FeetechBusError(f"no servo id {servo_id} on fake bus")
        if address == ADDR_PRESENT_POSITION:
            return encode_u16_le(self.present[servo_id])
        if address == ADDR_GOAL_POSITION:
            return encode_u16_le(self.goal[servo_id])
        if address == ADDR_TORQUE_ENABLE:
            return bytes((self.torque.get(servo_id, 0),))
        if address == 3:
            return encode_u16_le(self.model_number)
        return bytes(length)

    def _write(self, servo_id: int, address: int, data: bytes) -> None:
        self.writes.append({"id": servo_id, "address": address, "data": data})
        if address == ADDR_TORQUE_ENABLE:
            self.torque[servo_id] = data[0] if data else 0
        elif address == ADDR_GOAL_POSITION and len(data) >= 2:
            self.goal[servo_id] = decode_position(data)
            if self.torque.get(servo_id, 0):
                self.present[servo_id] = self.goal[servo_id]

    def close(self) -> None:
        self.closed = True


class FeetechBus:
    """Gripper-only view of a Feetech transport."""

    def __init__(self, transport: Transport, gripper_id: int = 6) -> None:
        if gripper_id in ARM_JOINT_IDS or gripper_id == BROADCAST_ID:
            raise ForbiddenServoCommand(
                f"gripper_id {gripper_id} must not be an arm joint 1-5 or broadcast"
            )
        self.transport = transport
        self.gripper_id = int(gripper_id)

    def ping(self) -> None:
        rx = self._send(self.gripper_id, INST_PING, b"")
        if not rx:
            raise FeetechBusError(
                _gazebo_refused(f"no ping reply from gripper id {self.gripper_id}")
            )

    def read_present_position(self) -> int:
        rx = self._send(
            self.gripper_id,
            INST_READ,
            bytes((ADDR_PRESENT_POSITION, 2)),
        )
        parsed = parse_instruction(rx)
        data = bytes(parsed["params"])
        # status: params are error's following bytes; parse_instruction treats
        # byte 4 as instruction/error and the rest as params. For a 2-byte
        # read, params == present-position bytes.
        if len(data) < 2:
            raise FeetechBusError("short present-position reply")
        return decode_position(data)

    def write_torque_enable(self, enable: bool) -> None:
        self._send(
            self.gripper_id,
            INST_WRITE,
            bytes((ADDR_TORQUE_ENABLE, 1 if enable else 0)),
        )

    def write_goal_position(self, ticks: int) -> None:
        ticks = max(POSITION_MIN, min(POSITION_MAX, int(ticks)))
        self._send(
            self.gripper_id,
            INST_WRITE,
            bytes((ADDR_GOAL_POSITION,)) + encode_u16_le(ticks),
        )

    def close(self) -> None:
        self.transport.close()

    def _send(self, servo_id: int, instruction: int, params: bytes) -> bytes:
        address = int(params[0]) if params else None
        assert_command_allowed(servo_id, instruction, self.gripper_id, address)
        if instruction == INST_PING:
            packet = ping_packet(servo_id)
        elif instruction == INST_READ:
            packet = read_packet(servo_id, int(params[0]), int(params[1]))
        else:
            packet = write_packet(servo_id, int(params[0]), bytes(params[1:]))
        return self.transport.transfer(packet)
