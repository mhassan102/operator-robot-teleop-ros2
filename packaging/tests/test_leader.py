"""Leader gripper fraction, without opening a serial port."""

from __future__ import annotations

import time

from packaging.operator_app.leader import (
    ADDR_MAX_POSITION,
    ADDR_MIN_POSITION,
    ADDR_PRESENT_POSITION,
    INST_READ,
    KEYBOARD_TOKENS,
    LeaderReader,
    encode_gripper_fraction,
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
