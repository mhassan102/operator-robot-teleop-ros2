"""Read-only SO-101 leader for the operator process.

The leader is plugged into the operator PC. This module never enables
torque and never writes a goal. Servo 6 becomes a fraction of the
leader's own travel, and the host sends that fraction on the existing
gripper float. The five keyboard values stay reserved, so a fraction
that would land on one of them is nudged by a few follower ticks.
Servos 1-6 are also read for the drive-page joint list. Those ticks
do not change the command.
"""

from __future__ import annotations

import glob
import logging
import math
import os
import select
import termios
import threading
import time
from pathlib import Path
from typing import Callable, Protocol

from packaging.operator_app.payload import JOINT_NAMES

log = logging.getLogger("teleop.leader")

GRIPPER_ID = 6
ADDR_MIN_POSITION = 9
ADDR_MAX_POSITION = 11
ADDR_MAX_TORQUE = 16
ADDR_PRESENT_POSITION = 56
INST_READ = 0x02
FOLLOWER_MAX_TORQUE = 500
# The leader cable on nvidia-3. The tty number changes. This id does not.
LEADER_SERIAL = "5B8E113252"
# Ignore leader jitter smaller than this. The first sample is published as-is.
DEADBAND_TICKS = 8
# EEPROM min is closed, matching follower tick 2036. Flip if an opening
# leader jaw lowers the ticks.
INVERT_LEADER_FRACTION = False
# Same five floats as teleop_demo.gripper_control.is_keyboard_gripper.
KEYBOARD_TOKENS = (0.0, 0.25, 0.5, 0.75, 1.0)
KEYBOARD_TOKEN_TOLERANCE = 1e-4
# About three ticks across the follower's 1430-tick travel.
TOKEN_NUDGE = 0.002
READ_TIMEOUT_S = 0.05
POLL_S = 0.05


class LeaderReadError(RuntimeError):
    """The leader gripper did not answer a read."""


class LeaderBus(Protocol):
    def read_u16(self, address: int) -> int:
        """Read one 16-bit register from gripper id 6."""

    def close(self) -> None:
        """Close the port. There is nothing to torque off."""


def encode_gripper_fraction(fraction: float) -> float:
    """A jaw fraction that is not one of the five keyboard floats.

    0 is closed and 1 is open. The robot treats the keyboard floats as
    one step or a hold, so those exact values are moved just off the token.
    """
    if not math.isfinite(fraction):
        return 0.5
    value = min(1.0, max(0.0, float(fraction)))
    for token in KEYBOARD_TOKENS:
        if abs(value - token) <= KEYBOARD_TOKEN_TOLERANCE:
            if token >= 1.0:
                return 1.0 - TOKEN_NUDGE
            if token <= 0.0:
                return TOKEN_NUDGE
            if token < 0.5:
                return token + TOKEN_NUDGE
            return token - TOKEN_NUDGE
    return value


def gripper_fraction(present: int, low: int, high: int) -> float:
    """Fraction of leader travel. EEPROM min is closed, matching the follower."""
    span = int(high) - int(low)
    if span <= 0:
        raise LeaderReadError(f"leader gripper limits {low}..{high}")
    position = min(int(high), max(int(low), int(present)))
    fraction = (position - int(low)) / span
    if INVERT_LEADER_FRACTION:
        return 1.0 - fraction
    return fraction


def _checksum(body: bytes) -> int:
    return (~sum(body)) & 0xFF


def read_packet(servo_id: int, address: int, length: int) -> bytes:
    """Protocol-0 read. This is the only instruction this module sends."""
    params = bytes((address & 0xFF, length & 0xFF))
    body = bytes((servo_id & 0xFF, len(params) + 2, INST_READ)) + params
    return bytes((0xFF, 0xFF)) + body + bytes((_checksum(body),))


def _known_leader_path() -> str | None:
    override = os.environ.get("TELEOP_LEADER_PORT", "").strip()
    if override:
        return override
    root = Path("/dev/serial/by-id")
    if not root.is_dir():
        return None
    matches = sorted(path for path in root.iterdir() if LEADER_SERIAL in path.name)
    if not matches:
        return None
    return str(matches[0])


def _candidate_paths() -> list[str]:
    found: list[str] = []
    root = Path("/dev/serial/by-id")
    if root.is_dir():
        found.extend(str(path) for path in sorted(root.iterdir()))
    found.extend(sorted(glob.glob("/dev/ttyACM*")))
    unique: list[str] = []
    seen: set[str] = set()
    for path in found:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


class SerialLeaderBus:
    """One gripper id, reads only, 1_000_000 8N1."""

    def __init__(self, path: str) -> None:
        self.path = path
        baud = getattr(termios, "B1000000", None)
        if baud is None:
            raise LeaderReadError("1000000 baud is not available")
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            attrs = termios.tcgetattr(fd)
            attrs[0] = 0
            attrs[1] = 0
            # Baud lives in ispeed/ospeed, as in feetech_bus. Putting it in
            # cflag as well is not how this tty accepts a 1_000_000 rate.
            attrs[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
            attrs[3] = 0
            attrs[4] = baud
            attrs[5] = baud
            attrs[6][termios.VMIN] = 0
            attrs[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            termios.tcflush(fd, termios.TCIOFLUSH)
        except Exception:
            os.close(fd)
            raise
        self._fd = fd

    def close(self) -> None:
        fd = self._fd
        self._fd = -1
        if fd >= 0:
            os.close(fd)

    def read_u16(self, address: int) -> int:
        packet = read_packet(GRIPPER_ID, address, 2)
        self._write(packet)
        data = self._read_status(GRIPPER_ID)
        if len(data) < 2:
            raise LeaderReadError(f"short leader reply for address {address}")
        raw = data[0] | (data[1] << 8)
        if address == ADDR_MAX_TORQUE:
            return raw
        return raw & 0x7FFF

    def read_present(self, servo_id: int) -> int:
        """Present position of one joint. Read only."""
        servo_id = int(servo_id)
        if servo_id < 1 or servo_id > GRIPPER_ID:
            raise LeaderReadError(f"leader servo {servo_id} is not a joint")
        packet = read_packet(servo_id, ADDR_PRESENT_POSITION, 2)
        self._write(packet)
        try:
            data = self._read_status(servo_id)
        except LeaderReadError:
            self._flush()
            raise
        if len(data) < 2:
            self._flush()
            raise LeaderReadError(f"short leader reply from servo {servo_id}")
        return (data[0] | (data[1] << 8)) & 0x7FFF

    def _flush(self) -> None:
        fd = self._fd
        if fd < 0:
            return
        try:
            termios.tcflush(fd, termios.TCIFLUSH)
        except OSError:
            pass

    def _write(self, packet: bytes) -> None:
        """Queue the read packet. This tty does not report writable to select."""
        fd = self._fd
        if fd < 0:
            raise LeaderReadError("leader port is closed")
        view = memoryview(packet)
        deadline = time.monotonic() + READ_TIMEOUT_S
        while view:
            try:
                wrote = os.write(fd, view)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LeaderReadError("leader port did not accept the read") from None
                time.sleep(0.002)
                continue
            if wrote <= 0:
                raise LeaderReadError("leader port did not accept the read")
            view = view[wrote:]

    def _read_status(self, servo_id: int) -> bytes:
        fd = self._fd
        if fd < 0:
            raise LeaderReadError("leader port is closed")
        deadline = time.monotonic() + READ_TIMEOUT_S
        buf = b""
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            ready, _, _ = select.select([fd], [], [], max(0.0, remaining))
            if not ready:
                break
            try:
                piece = os.read(fd, 64)
            except BlockingIOError:
                continue
            if not piece:
                break
            buf += piece
            data = _status_data(buf, servo_id)
            if data is not None:
                return data
            if len(buf) > 256:
                buf = buf[-8:]
        raise LeaderReadError(f"no reply from leader servo {servo_id}")


def _status_data(buf: bytes, servo_id: int) -> bytes | None:
    start = buf.find(b"\xff\xff")
    while start >= 0:
        packet = buf[start:]
        if len(packet) < 4:
            return None
        total = packet[3] + 4
        if len(packet) < total:
            return None
        frame = packet[:total]
        body = frame[2:-1]
        if frame[-1] == _checksum(body) and frame[2] == servo_id:
            return frame[5:-1]
        start = buf.find(b"\xff\xff", start + 2)
    return None


def _torque_is_leader(path: str) -> bool:
    bus: SerialLeaderBus | None = None
    try:
        bus = SerialLeaderBus(path)
        torque = bus.read_u16(ADDR_MAX_TORQUE)
    except (OSError, LeaderReadError):
        return False
    finally:
        if bus is not None:
            bus.close()
    return torque != FOLLOWER_MAX_TORQUE


def open_serial_leader() -> SerialLeaderBus:
    """Open the leader. Prefer the known cable, otherwise a gripper that is not the follower."""
    known = _known_leader_path()
    if known:
        return SerialLeaderBus(known)
    for path in _candidate_paths():
        if _torque_is_leader(path):
            return SerialLeaderBus(path)
    raise LeaderReadError("leader arm not found")


class LeaderReader:
    """Poll servo 6. ``fraction`` is None until a sample, and after repeated misses."""

    def __init__(self, open_bus: Callable[[], LeaderBus] | None = None) -> None:
        self._open_bus = open_serial_leader if open_bus is None else open_bus
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._fraction: float | None = None
        self._bus: LeaderBus | None = None
        self._low = 0
        self._high = 0
        self._published: int | None = None
        self._positions: dict[str, int] = {}
        self._misses = 0
        self._logged = 0.0

    def start(self, wait_s: float = 0.4) -> None:
        """Start reading. Wait briefly so the first command can carry a sample."""
        self.stop()
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, name="leader-gripper", daemon=True
        )
        self._thread.start()
        self._ready.wait(wait_s)

    def stop(self) -> None:
        self._stop.set()
        self._ready.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._close_bus()
        with self._lock:
            self._fraction = None
            self._positions = {}
        self._published = None
        self._misses = 0

    def fraction(self) -> float | None:
        with self._lock:
            return self._fraction

    def positions(self) -> dict[str, int]:
        """Latest present tick of each joint that has answered."""
        with self._lock:
            return dict(self._positions)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self._bus is None:
                    self._open()
                assert self._bus is not None
                present = self._bus.read_u16(ADDR_PRESENT_POSITION)
                self._remember(GRIPPER_ID, present)
                self._accept(present)
                self._read_arm(self._bus)
                self._misses = 0
                self._ready.set()
            except (OSError, LeaderReadError, AssertionError) as exc:
                self._misses += 1
                self._close_bus()
                if self._misses >= 3:
                    with self._lock:
                        self._fraction = None
                        self._positions = {}
                    self._published = None
                self._log_once(exc)
                if self._misses >= 2:
                    self._ready.set()
            if self._stop.wait(POLL_S):
                return

    def _open(self) -> None:
        bus = self._open_bus()
        try:
            low = bus.read_u16(ADDR_MIN_POSITION)
            high = bus.read_u16(ADDR_MAX_POSITION)
            if high <= low:
                raise LeaderReadError(f"leader gripper limits {low}..{high}")
        except Exception:
            bus.close()
            raise
        self._bus = bus
        self._low = low
        self._high = high
        self._published = None
        log.info("leader gripper limits %s..%s", low, high)

    def _accept(self, present: int) -> None:
        if (
            self._published is not None
            and abs(int(present) - self._published) < DEADBAND_TICKS
        ):
            return
        fraction = gripper_fraction(present, self._low, self._high)
        self._published = int(present)
        with self._lock:
            self._fraction = fraction
        log.info(
            "leader gripper ticks=%s fraction=%.3f", present, fraction
        )

    def _remember(self, servo_id: int, present: int) -> None:
        name = JOINT_NAMES[int(servo_id) - 1]
        with self._lock:
            self._positions[name] = int(present) & 0x7FFF

    def _read_arm(self, bus: LeaderBus) -> None:
        """Present ticks for the panel. A miss keeps the last sample."""
        reader = getattr(bus, "read_present", None)
        if reader is None:
            return
        for servo_id in range(1, GRIPPER_ID):
            try:
                present = reader(servo_id)
            except (OSError, LeaderReadError):
                continue
            self._remember(servo_id, present)

    def _close_bus(self) -> None:
        bus = self._bus
        self._bus = None
        if bus is not None:
            bus.close()

    def _log_once(self, exc: BaseException) -> None:
        now = time.monotonic()
        if now - self._logged < 1.0:
            return
        self._logged = now
        log.warning("leader gripper: %s", exc)
