"""Feetech SMS/STS (protocol 0) packets for the SO-ARM gripper.

Only ping, RAM read, torque-enable, and goal-position are allowed.
EEPROM writes, factory reset, broadcast, and arm joint IDs are refused.
"""

from __future__ import annotations

INST_PING = 0x01
INST_READ = 0x02
INST_WRITE = 0x03
INST_REG_WRITE = 0x04
INST_ACTION = 0x05
INST_RESET = 0x06
INST_SYNC_WRITE = 0x83

BROADCAST_ID = 0xFE

ADDR_MODEL_NUMBER = 3
ADDR_ID = 5
ADDR_BAUD_RATE = 6
ADDR_TORQUE_ENABLE = 40
ADDR_GOAL_POSITION = 42
ADDR_LOCK = 55
ADDR_PRESENT_POSITION = 56

POSITION_MIN = 0
POSITION_MAX = 4095
EEPROM_ADDR_MAX = 39

ALLOWED_INSTRUCTIONS = frozenset({INST_PING, INST_READ, INST_WRITE})
ALLOWED_WRITE_ADDRESSES = frozenset({ADDR_TORQUE_ENABLE, ADDR_GOAL_POSITION})
ALLOWED_READ_ADDRESSES = frozenset(
    {
        ADDR_MODEL_NUMBER,
        ADDR_TORQUE_ENABLE,
        ADDR_GOAL_POSITION,
        ADDR_PRESENT_POSITION,
    }
)

ARM_JOINT_IDS = frozenset({1, 2, 3, 4, 5})


class ForbiddenServoCommand(ValueError):
    """A packet would write EEPROM, reset, broadcast, or a non-gripper ID."""


def checksum(body: bytes | bytearray | list[int]) -> int:
    return (~sum(body)) & 0xFF


def build_packet(servo_id: int, instruction: int, params: bytes = b"") -> bytes:
    """Build an instruction packet. Length = instruction + params + checksum."""
    length = len(params) + 2
    body = bytes((servo_id, length, instruction)) + params
    return bytes((0xFF, 0xFF)) + body + bytes((checksum(body),))


def ping_packet(servo_id: int) -> bytes:
    return build_packet(servo_id, INST_PING)


def read_packet(servo_id: int, address: int, length: int) -> bytes:
    return build_packet(servo_id, INST_READ, bytes((address, length)))


def write_packet(servo_id: int, address: int, data: bytes) -> bytes:
    return build_packet(servo_id, INST_WRITE, bytes((address,)) + data)


def encode_u16_le(value: int) -> bytes:
    value = int(value) & 0xFFFF
    return bytes((value & 0xFF, (value >> 8) & 0xFF))


def decode_u16_le(data: bytes) -> int:
    return data[0] | (data[1] << 8)


def decode_position(data: bytes) -> int:
    """STS present/goal position: 12-bit magnitude, bit 15 is the sign flag."""
    raw = decode_u16_le(data)
    return raw & 0x7FFF


def status_packet(servo_id: int, error: int = 0, data: bytes = b"") -> bytes:
    length = len(data) + 2
    body = bytes((servo_id, length, error)) + data
    return bytes((0xFF, 0xFF)) + body + bytes((checksum(body),))


def parse_instruction(packet: bytes) -> dict[str, object]:
    if len(packet) < 6:
        raise ValueError(f"short Feetech packet ({len(packet)} bytes)")
    if packet[0] != 0xFF or packet[1] != 0xFF:
        raise ValueError("Feetech packet missing 0xFF 0xFF header")
    servo_id = packet[2]
    length = packet[3]
    expected = length + 4
    if len(packet) < expected:
        raise ValueError("truncated Feetech packet")
    instruction = packet[4]
    params = packet[5:expected - 1]
    got = packet[expected - 1]
    body = packet[2:expected - 1]
    if got != checksum(body):
        raise ValueError("Feetech checksum mismatch")
    address = int(params[0]) if params else None
    data = bytes(params[1:]) if len(params) > 1 else b""
    return {
        "id": servo_id,
        "instruction": instruction,
        "address": address,
        "data": data,
        "params": bytes(params),
    }


def assert_command_allowed(
    servo_id: int,
    instruction: int,
    gripper_id: int,
    address: int | None = None,
) -> None:
    """Refuse anything that could move joints 1-5 or touch EEPROM/identity."""
    if servo_id == BROADCAST_ID:
        raise ForbiddenServoCommand(
            f"broadcast ID {BROADCAST_ID:#x} is forbidden; gripper id is {gripper_id}"
        )
    if servo_id != gripper_id:
        raise ForbiddenServoCommand(
            f"servo id {servo_id} is not gripper id {gripper_id}; "
            "refusing to command arm joints 1-5"
        )
    if servo_id in ARM_JOINT_IDS:
        raise ForbiddenServoCommand(
            f"servo id {servo_id} is an arm joint; torque/goal writes are gripper-only"
        )
    if instruction == INST_RESET:
        raise ForbiddenServoCommand("factory reset is forbidden")
    if instruction not in ALLOWED_INSTRUCTIONS:
        raise ForbiddenServoCommand(
            f"instruction {instruction:#x} is forbidden (ping/read/write only)"
        )
    if instruction == INST_WRITE:
        if address is None:
            raise ForbiddenServoCommand("write requires a register address")
        if address <= EEPROM_ADDR_MAX or address == ADDR_LOCK:
            raise ForbiddenServoCommand(
                f"EEPROM/lock write to address {address} is forbidden"
            )
        if address not in ALLOWED_WRITE_ADDRESSES:
            raise ForbiddenServoCommand(
                f"write to address {address} is forbidden (torque/goal only)"
            )
    if instruction == INST_READ and address is not None:
        if address not in ALLOWED_READ_ADDRESSES:
            raise ForbiddenServoCommand(f"read of address {address} is not allowed")
