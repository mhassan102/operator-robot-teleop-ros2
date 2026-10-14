"""Leader gripper fraction, without opening a serial port."""

from __future__ import annotations

import time

from packaging.operator_app.leader import (
    ADDR_MAX_POSITION,
    ADDR_MIN_POSITION,
    ADDR_PRESENT_POSITION,
    INST_READ,
    KEYBOARD_TOKENS,
    LeaderReadError,
    LeaderReader,
    encode_gripper_fraction,
    encode_joint_fraction,
    gripper_fraction,
    read_packet,
)


def test_read_packet_is_only_a_read() -> None:
    packet = read_packet(6, ADDR_PRESENT_POSITION, 2)
    assert packet[:2] == b"\xff\xff"
    assert packet[2] == 6
    assert packet[4] == INST_READ


def test_fraction_uses_leader_travel_and_encode_avoids_keyboard_tokens() -> None:
    assert gripper_fraction(2037, 2037, 3216) == 0.0
    assert gripper_fraction(3216, 2037, 3216) == 1.0
    assert abs(gripper_fraction(2150, 2037, 3216) - (113 / 1179)) < 1e-9
    for token in KEYBOARD_TOKENS:
        encoded = encode_gripper_fraction(token)
        assert encoded not in KEYBOARD_TOKENS
        assert abs(encoded - token) < 0.01
    assert encode_gripper_fraction(0.4) == 0.4


def test_joint_fraction_keeps_endpoints_and_nudges_only_hold() -> None:
    assert encode_joint_fraction(0.0) == 0.0
    assert encode_joint_fraction(1.0) == 1.0
    assert encode_joint_fraction(0.2) == 0.2
    nudged = encode_joint_fraction(0.5)
    assert nudged != 0.5
    assert abs(nudged - 0.5) < 0.01
    assert encode_joint_fraction(nudged) == nudged
    assert encode_joint_fraction(2.0) == 1.0
    assert encode_joint_fraction(float("nan")) == 0.5


class _Bus:
    def __init__(self) -> None:
        self.present = 2150
        self.closed = False

    def read_u16(self, address: int) -> int:
        if address == ADDR_MIN_POSITION:
            return 2037
        if address == ADDR_MAX_POSITION:
            return 3216
        if address == ADDR_PRESENT_POSITION:
            return self.present
        raise AssertionError(address)

    def close(self) -> None:
        self.closed = True


def test_reader_publishes_the_opening_and_ignores_jitter() -> None:
    bus = _Bus()
    reader = LeaderReader(open_bus=lambda: bus)
    reader.start(wait_s=1.0)
    try:
        sample = reader.fraction()
        assert sample is not None
        assert abs(sample - (113 / 1179)) < 1e-9
        assert reader.arm_fractions() == [None, None, None, None, None]
        bus.present = 2154
        time.sleep(0.15)
        assert reader.fraction() == sample
        bus.present = 2200
        deadline = time.monotonic() + 1.0
        updated = sample
        while time.monotonic() < deadline:
            updated = reader.fraction()
            if updated != sample:
                break
            time.sleep(0.05)
        assert updated is not None
        assert abs(updated - ((2200 - 2037) / 1179)) < 1e-9
    finally:
        reader.stop()
    assert bus.closed


class _ArmBus(_Bus):
    """Jaw registers plus min/max and present for servos 1-5."""

    def __init__(self) -> None:
        super().__init__()
        self.arm_present = {1: 300, 2: 50, 3: 10, 4: 70, 5: 130}

    def read_register(self, servo_id: int, address: int) -> int:
        if servo_id == 4:
            raise LeaderReadError("servo 4 missed")
        limits = {1: (100, 500), 2: (0, 0), 3: (10, 110), 5: (30, 130)}
        low, high = limits[servo_id]
        if address == ADDR_MIN_POSITION:
            return low
        if address == ADDR_MAX_POSITION:
            return high
        raise AssertionError((servo_id, address))

    def read_present(self, servo_id: int) -> int:
        return self.arm_present[servo_id]


def test_reader_publishes_arm_fractions_and_skips_unknown_limits() -> None:
    bus = _ArmBus()
    reader = LeaderReader(open_bus=lambda: bus)
    reader.start(wait_s=1.0)
    try:
        sample = reader.fraction()
        assert sample is not None
        fractions = reader.arm_fractions()
        assert fractions[0] == encode_joint_fraction(0.5)
        assert fractions[0] != 0.5
        assert fractions[1] is None
        assert fractions[2] == 0.0
        assert fractions[3] is None
        assert fractions[4] == 1.0
        bus.arm_present[1] = 302
        time.sleep(0.15)
        assert reader.arm_fractions()[0] == fractions[0]
        bus.arm_present[1] = 320
        deadline = time.monotonic() + 1.0
        updated = fractions[0]
        while time.monotonic() < deadline:
            updated = reader.arm_fractions()[0]
            if updated != fractions[0]:
                break
            time.sleep(0.05)
        assert updated == encode_joint_fraction((320 - 100) / 400)
    finally:
        reader.stop()
    assert bus.closed
