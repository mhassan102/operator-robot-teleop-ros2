"""Fake-bus tests for the Feetech gripper: ID filter, deadman, tiny delta."""

from __future__ import annotations

import os
import pty

import pytest

from teleop_demo.feetech_bus import (
    FakeTransport,
    FeetechBus,
    SerialBusyError,
    SerialMissingError,
    SerialTransport,
)
from teleop_demo.feetech_protocol import (
    ADDR_BAUD_RATE,
    ADDR_GOAL_POSITION,
    ADDR_ID,
    ADDR_LOCK,
    ADDR_TORQUE_ENABLE,
    ARM_JOINT_IDS,
    BROADCAST_ID,
    ForbiddenServoCommand,
    INST_PING,
    INST_RESET,
    INST_SYNC_WRITE,
    INST_WRITE,
    assert_command_allowed,
    parse_instruction,
    ping_packet,
)
from teleop_demo.gripper_control import (
    CONNECTED,
    DEFAULT_MAX_DELTA_TICKS,
    SAFE_STOP,
    TIMEOUT,
    VERIFIED_GRIPPER_ID,
    VERIFIED_RANGE_MAX,
    VERIFIED_RANGE_MIN,
    WATCHDOG_OK,
    GripperActuator,
    GripperController,
    GripperOutput,
    clamp_tiny_delta,
    map_gripper_to_ticks,
)


def _bus(present: int = 2061) -> tuple[FakeTransport, FeetechBus]:
    transport = FakeTransport(present={VERIFIED_GRIPPER_ID: present})
    return transport, FeetechBus(transport, gripper_id=VERIFIED_GRIPPER_ID)


def _live(controller: GripperController, value: float, now_s: float) -> None:
    controller.on_teleop_state(CONNECTED, WATCHDOG_OK)
    controller.on_gripper_safe(value, now_s)


def _arm(controller: GripperController, present: int, now_s: float = 0.0) -> None:
    """Link goes live on hold. That latch is not a step."""
    _live(controller, 0.5, now_s)
    held = controller.tick(now_s + 0.001, present)
    assert held.deadman is False
    assert held.goal_position == present


def test_verified_calibration_constants() -> None:
    assert VERIFIED_GRIPPER_ID == 6
    assert VERIFIED_RANGE_MIN == 2036
    assert VERIFIED_RANGE_MAX == 3466
    assert DEFAULT_MAX_DELTA_TICKS == 48
    assert GripperController().command_timeout_s == 0.5
    assert 1 in ARM_JOINT_IDS and 6 not in ARM_JOINT_IDS


def test_ping_packet_matches_hardware_capture() -> None:
    packet = ping_packet(6)
    assert packet == bytes.fromhex("ffff060201f6")


def test_id_filter_blocks_arm_joints_and_broadcast() -> None:
    for servo_id in (1, 2, 3, 4, 5, BROADCAST_ID):
        with pytest.raises(ForbiddenServoCommand):
            assert_command_allowed(servo_id, INST_WRITE, 6, ADDR_GOAL_POSITION)
        with pytest.raises(ForbiddenServoCommand):
            assert_command_allowed(servo_id, INST_WRITE, 6, ADDR_TORQUE_ENABLE)
    assert_command_allowed(6, INST_WRITE, 6, ADDR_GOAL_POSITION)
    assert_command_allowed(6, INST_PING, 6)


def test_eeprom_reset_and_lock_writes_forbidden() -> None:
    with pytest.raises(ForbiddenServoCommand, match="factory reset"):
        assert_command_allowed(6, INST_RESET, 6)
    with pytest.raises(ForbiddenServoCommand):
        assert_command_allowed(6, INST_SYNC_WRITE, 6, ADDR_GOAL_POSITION)
    for address in (ADDR_ID, ADDR_BAUD_RATE, 0, 39, ADDR_LOCK):
        with pytest.raises(ForbiddenServoCommand):
            assert_command_allowed(6, INST_WRITE, 6, address)


def test_bus_public_api_writes_gripper_id_only() -> None:
    transport, bus = _bus()
    bus.ping()
    bus.write_torque_enable(True)
    bus.write_goal_position(2100)
    written_ids = {int(item["id"]) for item in transport.writes}
    assert written_ids == {6}
    addresses = {int(item["address"]) for item in transport.writes}
    assert addresses <= {ADDR_TORQUE_ENABLE, ADDR_GOAL_POSITION}
    for packet in transport.packets:
        parsed = parse_instruction(packet)
        assert int(parsed["id"]) == 6


def test_bus_rejects_wrong_id_even_via_send() -> None:
    _transport, bus = _bus()
    with pytest.raises(ForbiddenServoCommand, match="not gripper"):
        bus._send(1, INST_WRITE, bytes((ADDR_GOAL_POSITION, 0, 8)))


def test_ping_and_present_position_on_fake_bus() -> None:
    _transport, bus = _bus(present=2061)
    bus.ping()
    assert bus.read_present_position() == 2061


def test_missing_serial_refuses_gazebo() -> None:
    with pytest.raises(SerialMissingError, match="refusing to fall back to Gazebo"):
        SerialTransport.open_exclusive("/dev/ttyACM0_stage2_does_not_exist")


def test_exclusive_open_second_owner_fails() -> None:
    master, slave = pty.openpty()
    try:
        path = os.ttyname(slave)
        first = SerialTransport.open_exclusive(path, baudrate=115200)
        try:
            with pytest.raises(SerialBusyError, match="busy"):
                SerialTransport.open_exclusive(path, baudrate=115200)
        finally:
            first.close()
    finally:
        os.close(master)
        os.close(slave)


def test_map_close_open_to_calibrated_range() -> None:
    assert map_gripper_to_ticks(0.0) == VERIFIED_RANGE_MIN
    assert map_gripper_to_ticks(1.0) == VERIFIED_RANGE_MAX
    mid = map_gripper_to_ticks(0.5)
    assert VERIFIED_RANGE_MIN < mid < VERIFIED_RANGE_MAX


def test_tiny_delta_does_not_slam() -> None:
    anchor = 2061
    target = map_gripper_to_ticks(1.0)
    assert target == VERIFIED_RANGE_MAX
    clamped = clamp_tiny_delta(target, anchor, DEFAULT_MAX_DELTA_TICKS)
    assert clamped == anchor + DEFAULT_MAX_DELTA_TICKS
    assert clamped - anchor <= DEFAULT_MAX_DELTA_TICKS


def test_deadman_without_gripper_safe_disables_torque() -> None:
    transport, bus = _bus()
    controller = GripperController(command_timeout_s=0.5)
    actuator = GripperActuator(bus)
    output = controller.tick(0.0, 2061)
    assert output.deadman
    assert output.torque_enable is False
    assert output.goal_position is None
    actuator.apply(output)
    assert transport.torque[6] == 0
    assert transport.writes[-1]["address"] == ADDR_TORQUE_ENABLE
    assert bytes(transport.writes[-1]["data"]) == b"\x00"


def test_watchdog_state_zeros_torque_and_does_not_hold_close() -> None:
    transport, bus = _bus(present=2061)
    controller = GripperController(command_timeout_s=0.5)
    actuator = GripperActuator(bus)
    _arm(controller, 2061, 0.0)
    actuator.apply(controller.tick(0.01, 2061))
    controller.on_gripper_safe(1.0, 0.02)
    live = controller.tick(0.03, 2061)
    assert live.deadman is False
    actuator.apply(live)
    assert transport.torque[6] == 1
    live_goal = int(transport.goal[6])
    assert live_goal == 2061 + DEFAULT_MAX_DELTA_TICKS

    controller.on_teleop_state(TIMEOUT, SAFE_STOP)
    dead = controller.tick(0.04, live_goal)
    assert dead.deadman
    assert dead.goal_position is None
    actuator.apply(dead)
    assert transport.torque[6] == 0
    goals_after = [
        item for item in transport.writes if int(item["address"]) == ADDR_GOAL_POSITION
    ]
    assert goals_after
    last_write = transport.writes[-1]
    assert int(last_write["address"]) == ADDR_TORQUE_ENABLE
    assert bytes(last_write["data"]) == b"\x00"


def test_gripper_safe_silence_is_deadman() -> None:
    controller = GripperController(command_timeout_s=0.5)
    _live(controller, 1.0, 0.0)
    live = controller.tick(0.1, 2061)
    assert live.deadman is False
    silent = controller.tick(0.6, 2061)
    assert silent.deadman
    assert silent.torque_enable is False


def test_full_open_command_stays_within_one_step() -> None:
    controller = GripperController()
    _arm(controller, 2061)
    controller.on_gripper_safe(1.0, 0.1)
    output = controller.tick(0.11, 2061)
    assert output.goal_position == 2061 + DEFAULT_MAX_DELTA_TICKS
    assert output.goal_position < VERIFIED_RANGE_MAX


def test_close_command_does_not_pass_range_min() -> None:
    controller = GripperController()
    _arm(controller, 2061)
    controller.on_gripper_safe(0.25, 0.1)
    output = controller.tick(0.11, 2061)
    assert output.goal_position == VERIFIED_RANGE_MIN
    assert output.goal_position >= VERIFIED_RANGE_MIN


def _press(
    controller: GripperController, value: float, present: int, now_s: float
) -> GripperOutput:
    controller.on_gripper_safe(value, now_s)
    return controller.tick(now_s + 0.001, present)


def test_presses_accumulate_and_a_latched_repeat_does_not_reset() -> None:
    controller = GripperController()
    present = 2061
    _arm(controller, present)
    first = _press(controller, 1.0, present, 0.1)
    assert first.goal_position == present + 48
    for index in range(5):
        repeated = _press(controller, 1.0, present, 0.2 + index * 0.01)
        assert repeated.goal_position == present + 48
    second = _press(controller, 0.75, 9999, 0.4)
    assert second.goal_position == present + 96
    third = _press(controller, 1.0, 9999, 0.5)
    assert third.goal_position == present + 144
    assert third.goal_position == 2205
    for index in range(5):
        latched = _press(controller, 1.0, 1111, 0.6 + index * 0.01)
        assert latched.goal_position == present + 144


def test_two_closes_then_hold_and_keyup_do_not_reverse() -> None:
    controller = GripperController()
    present = 3000
    _arm(controller, present)
    first = _press(controller, 0.25, present, 0.1)
    assert first.goal_position == present - 48
    held = _press(controller, 0.25, present, 0.2)
    assert held.goal_position == present - 48
    second = _press(controller, 0.0, 9999, 0.3)
    assert second.goal_position == present - 96
    keyup = _press(controller, 0.5, 9999, 0.4)
    assert keyup.goal_position == present - 96


def test_link_up_holds_and_close_stays_through_release_to_the_rail() -> None:
    """Console start and keyup must not open. Close walks to the closed stop."""
    controller = GripperController()
    present = 3300
    _arm(controller, present)
    assert controller.tick(0.02, present).goal_position == present
    assert _press(controller, 1.0, present, 0.1).goal_position == present + 48
    assert _press(controller, 0.5, present, 0.2).goal_position == present + 48
    assert _press(controller, 0.25, present, 0.3).goal_position == present
    assert _press(controller, 0.25, 9999, 0.4).goal_position == present
    assert _press(controller, 0.5, 9999, 0.5).goal_position == present

    controller.on_teleop_state(TIMEOUT, SAFE_STOP)
    dead = controller.tick(0.6, present)
    assert dead.deadman is True
    assert dead.goal_position is None
    assert dead.torque_enable is False
    controller.on_teleop_state(CONNECTED, WATCHDOG_OK)
    controller.on_gripper_safe(0.5, 0.7)
    resumed = controller.tick(0.71, present)
    assert resumed.deadman is False
    assert resumed.goal_position == present
    assert controller.goal is None

    rail = GripperController()
    jaw = VERIFIED_RANGE_MAX
    _arm(rail, jaw)
    token = 0.25
    now = 0.1
    for _ in range(40):
        stepped = _press(rail, token, jaw, now)
        now += 0.05
        assert stepped.goal_position is not None
        assert stepped.goal_position <= jaw
        jaw = stepped.goal_position
        held = _press(rail, 0.5, jaw, now)
        now += 0.05
        assert held.goal_position == jaw
        token = 0.0 if token == 0.25 else 0.25
        if jaw == VERIFIED_RANGE_MIN:
            break
    assert jaw == VERIFIED_RANGE_MIN
    assert _press(rail, 0.25, jaw, now).goal_position == VERIFIED_RANGE_MIN
    assert (
        _press(rail, 1.0, jaw, now + 0.05).goal_position
        == VERIFIED_RANGE_MIN + DEFAULT_MAX_DELTA_TICKS
    )


def test_open_and_close_stop_at_the_verified_ends() -> None:
    controller = GripperController()
    _arm(controller, VERIFIED_RANGE_MAX)
    blocked = _press(controller, 1.0, VERIFIED_RANGE_MAX, 0.1)
    assert blocked.goal_position == VERIFIED_RANGE_MAX
    again = _press(controller, 0.75, VERIFIED_RANGE_MAX, 0.2)
    assert again.goal_position == VERIFIED_RANGE_MAX
    opposite = _press(controller, 0.25, VERIFIED_RANGE_MAX, 0.3)
    assert opposite.goal_position == VERIFIED_RANGE_MAX - DEFAULT_MAX_DELTA_TICKS

    low = GripperController()
    _arm(low, VERIFIED_RANGE_MIN)
    closed = _press(low, 0.25, VERIFIED_RANGE_MIN, 0.1)
    assert closed.goal_position == VERIFIED_RANGE_MIN
    still = _press(low, 0.0, VERIFIED_RANGE_MIN, 0.2)
    assert still.goal_position == VERIFIED_RANGE_MIN
    opened = _press(low, 1.0, VERIFIED_RANGE_MIN, 0.3)
    assert opened.goal_position == VERIFIED_RANGE_MIN + DEFAULT_MAX_DELTA_TICKS


def test_partial_step_clamps_to_the_verified_range() -> None:
    controller = GripperController()
    _arm(controller, 3460)
    opened = _press(controller, 1.0, 3460, 0.1)
    assert opened.goal_position == VERIFIED_RANGE_MAX
    low = GripperController()
    _arm(low, 2050)
    closed = _press(low, 0.25, 2050, 0.1)
    assert closed.goal_position == VERIFIED_RANGE_MIN


def test_goal_also_clamps_to_servo_position_limits() -> None:
    wide = GripperController(range_min=-100, range_max=5000)
    _arm(wide, 10)
    closed = _press(wide, 0.25, 10, 0.1)
    assert closed.goal_position == 0
    high = GripperController(range_min=-100, range_max=5000)
    _arm(high, 4080)
    opened = _press(high, 1.0, 4080, 0.1)
    assert opened.goal_position == 4095


def test_deadman_clears_the_goal_and_the_next_press_steps_from_present() -> None:
    transport, bus = _bus(present=2061)
    controller = GripperController(command_timeout_s=0.5)
    actuator = GripperActuator(bus)
    _arm(controller, 2061, 0.0)
    actuator.apply(controller.tick(0.01, 2061))
    controller.on_gripper_safe(1.0, 0.02)
    actuator.apply(controller.tick(0.03, 2061))
    assert int(transport.goal[6]) == 2061 + DEFAULT_MAX_DELTA_TICKS
    goals_before = sum(
        1 for item in transport.writes if int(item["address"]) == ADDR_GOAL_POSITION
    )

    controller.on_teleop_state(TIMEOUT, SAFE_STOP)
    dead = controller.tick(0.04, 2109)
    assert dead.deadman
    assert dead.goal_position is None
    assert controller.goal is None
    actuator.apply(dead)
    assert transport.torque[6] == 0
    goals_after = sum(
        1 for item in transport.writes if int(item["address"]) == ADDR_GOAL_POSITION
    )
    assert goals_after == goals_before
    assert int(transport.writes[-1]["address"]) == ADDR_TORQUE_ENABLE
    assert bytes(transport.writes[-1]["data"]) == b"\x00"
    assert all(int(item["id"]) == 6 for item in transport.writes)

    controller.on_teleop_state(CONNECTED, WATCHDOG_OK)
    controller.on_gripper_safe(1.0, 0.05)
    stale = controller.tick(0.06, 2500)
    assert stale.deadman is False
    assert stale.goal_position == 2500
    assert controller.goal is None
    repeated = _press(controller, 1.0, 2500, 0.07)
    assert repeated.goal_position == 2500
    assert controller.goal is None
    stepped = _press(controller, 0.75, 2500, 0.08)
    assert stepped.goal_position == 2500 + DEFAULT_MAX_DELTA_TICKS
    actuator.apply(stepped)
    assert int(transport.goal[6]) == 2500 + DEFAULT_MAX_DELTA_TICKS
    assert all(int(item["id"]) == 6 for item in transport.writes)


def test_gripper_safe_without_state_is_deadman() -> None:
    controller = GripperController()
    controller.on_gripper_safe(1.0, 0.0)
    output = controller.tick(0.01, 2061)
    assert output.deadman
    assert output.torque_enable is False
    assert output.goal_position is None


def test_actuator_does_not_write_goal_while_deadman() -> None:
    transport, bus = _bus()
    actuator = GripperActuator(bus)
    controller = GripperController()
    controller.on_teleop_state(CONNECTED, WATCHDOG_OK)
    controller.on_gripper_safe(1.0, 0.0)
    actuator.apply(controller.tick(0.01, 2061))
    n_goals = sum(
        1 for item in transport.writes if int(item["address"]) == ADDR_GOAL_POSITION
    )
    controller.on_teleop_state(TIMEOUT, SAFE_STOP)
    actuator.apply(controller.tick(0.02, 2061))
    n_goals_after = sum(
        1 for item in transport.writes if int(item["address"]) == ADDR_GOAL_POSITION
    )
    assert n_goals_after == n_goals
    assert all(int(item["id"]) == 6 for item in transport.writes)


def test_gripper_id_cannot_be_an_arm_joint() -> None:
    transport = FakeTransport()
    with pytest.raises(ForbiddenServoCommand):
        FeetechBus(transport, gripper_id=1)
