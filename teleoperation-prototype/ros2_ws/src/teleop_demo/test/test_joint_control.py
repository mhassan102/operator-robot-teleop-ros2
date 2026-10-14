"""Limited steps for follower servos 1-5. No serial port and no arm."""

from __future__ import annotations

from teleop_demo.feetech_bus import FakeTransport, FeetechBus
from teleop_demo.feetech_protocol import (
    ADDR_GOAL_POSITION,
    ADDR_TORQUE_ENABLE,
    BROADCAST_ID,
    INST_SYNC_WRITE,
    parse_instruction,
)
from teleop_demo.gripper_control import (
    CONNECTED,
    DEFAULT_MAX_DELTA_TICKS,
    SAFE_STOP,
    TIMEOUT,
    WATCHDOG_OK,
)
from teleop_demo.joint_control import (
    JOINT_HOLD,
    ArmOutput,
    JointActuator,
    JointArmController,
    JointGoal,
)


def _controller(*ranges: tuple[int, int] | None) -> JointArmController:
    controller = JointArmController()
    for index in range(5):
        if index < len(ranges) and ranges[index] is not None:
            low, high = ranges[index]  # type: ignore[misc]
            controller.set_limits(index, low, high)
    return controller


def _live(controller: JointArmController, values: list[float], now_s: float) -> None:
    controller.on_teleop_state(CONNECTED, WATCHDOG_OK)
    controller.on_command(values, now_s)


def _hold() -> list[float]:
    return [JOINT_HOLD] * 5


def test_unknown_limits_and_a_present_outside_them_do_not_move() -> None:
    controller = _controller()
    _live(controller, [2.0] * 5, 0.0)
    blocked = controller.tick(0.01, [1000] * 5)
    assert blocked.deadman is False
    assert all(joint.goal_position is None and joint.torque_enable is False for joint in blocked.joints)
    outside = _controller((100, 200), None, None, None, None)
    _live(outside, [2.0, 0.5, 0.5, 0.5, 0.5], 0.0)
    output = outside.tick(0.01, [50, None, None, None, None])
    assert output.joints[0].goal_position is None
    assert output.joints[0].torque_enable is False
    empty = _controller()
    empty.set_limits(0, 5, 5)
    _live(empty, [2.0, 0.5, 0.5, 0.5, 0.5], 0.0)
    assert empty.tick(0.01, [5, 0, 0, 0, 0]).joints[0].torque_enable is False


def test_keyboard_jog_steps_once_per_command_and_stops_at_the_limit() -> None:
    controller = _controller(*[(0, 4000)] * 5)
    values = _hold()
    values[1] = 2.0
    _live(controller, values, 0.0)
    first = controller.tick(0.01, [1000] * 5)
    assert first.joints[1].servo_id == 2
    assert first.joints[1].goal_position == 1000 + DEFAULT_MAX_DELTA_TICKS
    assert first.joints[1].torque_enable is True
    assert first.joints[0].goal_position == 1000
    caught_up = controller.tick(0.02, [1000, 1048, 1000, 1000, 1000])
    assert caught_up.joints[1].goal_position == 1048
    controller.on_command(values, 0.03)
    again = controller.tick(0.04, [1000, 1048, 1000, 1000, 1000])
    assert again.joints[1].goal_position == 1096
    controller.on_command(values, 0.05)
    glitch = controller.tick(0.06, [1000, 3000, 1000, 1000, 1000])
    assert glitch.joints[1].goal_position == 1096 + DEFAULT_MAX_DELTA_TICKS
    controller.on_command(_hold(), 0.07)
    held = controller.tick(0.08, [1000, 2000, 1000, 1000, 1000])
    assert held.joints[1].goal_position == 1144
    high = _controller((0, 4000))
    _live(high, [2.0, 0.5, 0.5, 0.5, 0.5], 0.0)
    assert high.tick(0.01, [3980, 0, 0, 0, 0]).joints[0].goal_position == 4000
    low = _controller((0, 4000))
    _live(low, [-1.0, 0.5, 0.5, 0.5, 0.5], 0.0)
    assert low.tick(0.01, [20, 0, 0, 0, 0]).joints[0].goal_position == 0
    tokens = _controller(*[(0, 4000)] * 5)
    _live(tokens, [1.5, -0.5, 0.5, 0.5, 0.5], 0.0)
    token_step = tokens.tick(0.01, [1000, 1000, 0, 0, 0])
    assert token_step.joints[0].goal_position == 1048
    assert token_step.joints[1].goal_position == 952


def test_same_jog_does_not_keep_stepping_after_the_packets_stop() -> None:
    controller = _controller(*[(0, 4000)] * 5)
    values = _hold()
    values[1] = 2.0
    _live(controller, values, 0.0)
    assert controller.tick(0.01, [1000] * 5).joints[1].goal_position == 1048
    for step in range(8):
        present = 1048 + step * DEFAULT_MAX_DELTA_TICKS
        output = controller.tick(0.02 + step * 0.01, [1000, present, 1000, 1000, 1000])
        assert output.deadman is False
        assert output.joints[1].goal_position == 1048
    silent = controller.tick(0.6, [1000, 1400, 1000, 1000, 1000])
    assert silent.deadman is True
    assert silent.joints[1].goal_position is None
    assert silent.joints[1].torque_enable is False


def test_leader_fraction_walks_one_step_per_tick_and_then_holds() -> None:
    controller = _controller(*[(0, 4000)] * 5)
    _live(controller, [1.0, 0.0, 0.5, 3.0, 0.5], 0.0)
    present = [1000, 1000, 1000, 1000, 1000]
    first = controller.tick(0.01, present)
    assert first.joints[0].goal_position == 1048
    assert first.joints[1].goal_position == 952
    assert first.joints[3].goal_position is None
    previous = 1048
    for _ in range(70):
        output = controller.tick(0.02, [previous, 952, 1000, 1000, 1000])
        goal = output.joints[0].goal_position
        assert goal is not None
        assert 0 < goal - previous <= DEFAULT_MAX_DELTA_TICKS or goal == previous
        previous = goal
        if goal == 4000:
            break
    assert previous == 4000
    assert controller.tick(0.03, [4000, 0, 1000, 1000, 1000]).joints[0].goal_position == 4000
    assert controller.command_timeout_s == 0.5
    assert DEFAULT_MAX_DELTA_TICKS == 48


def test_deadman_clears_goals_and_the_actuator_writes_no_goal() -> None:
    transport = FakeTransport()
    bus = FeetechBus(transport)
    actuator = JointActuator(bus)
    controller = _controller(*[(0, 4000)] * 5)
    _live(controller, [0.5, 2.0, 0.5, 0.5, 0.5], 0.0)
    live = controller.tick(0.01, [1000] * 5)
    actuator.apply(live)
    assert transport.torque[2] == 1
    goals = [item for item in transport.writes if int(item["address"]) == ADDR_GOAL_POSITION]
    assert goals
    assert all(int(item["id"]) != 6 for item in transport.writes)

    controller.on_teleop_state(TIMEOUT, SAFE_STOP)
    dead = controller.tick(0.02, [1048] * 5)
    assert dead.deadman is True
    assert all(joint.goal_position is None for joint in dead.joints)
    actuator.apply(dead)
    goals_after = [item for item in transport.writes if int(item["address"]) == ADDR_GOAL_POSITION]
    assert goals_after == goals
    assert transport.torque[2] == 0
    assert int(transport.writes[-1]["address"]) == ADDR_TORQUE_ENABLE
    assert bytes(transport.writes[-1]["data"]) == b"\x00"
    assert all(int(item["id"]) != 6 for item in transport.writes)

    quiet = _controller(*[(0, 4000)] * 5)
    quiet.on_teleop_state(CONNECTED, WATCHDOG_OK)
    quiet.on_command(_hold(), 0.0)
    assert quiet.tick(0.51, [1000] * 5).deadman is True
    missing = JointArmController()
    missing.set_limits(0, 0, 4000)
    missing.on_command([2.0, 0.5, 0.5, 0.5, 0.5], 0.0)
    assert "teleop/state" in missing.tick(0.01, [1000] * 5).reason
    bad = _controller((0, 4000))
    bad.on_teleop_state(CONNECTED, WATCHDOG_OK)
    bad.on_command([float("nan"), 0.5, 0.5, 0.5, 0.5], 0.0)
    assert bad.tick(0.01, [1000] * 5).deadman is True


def test_actuator_refuses_a_jump_and_never_writes_the_jaw() -> None:
    transport = FakeTransport()
    for servo_id in range(1, 6):
        transport.present[servo_id] = 1000
    bus = FeetechBus(transport)
    actuator = JointActuator(bus)
    far = ArmOutput(
        deadman=False,
        reason="live",
        joints=(JointGoal(1, True, 2000, 1000), JointGoal(6, True, 3000, 2061)),
    )
    actuator.apply(far)
    assert transport.writes == []

    actuator.last_goal[1] = 1000
    actuator.last_torque[1] = True
    actuator.apply(far)
    assert transport.writes == []

    controller = _controller(*[(0, 4000)] * 5)
    _live(controller, [1.0] * 5, 0.0)
    walked = controller.tick(0.01, [1000] * 5)
    extra = walked.joints + (JointGoal(6, True, 3466, 2061),)
    actuator.apply(ArmOutput(False, "live", extra))
    ids = {int(item["id"]) for item in transport.writes}
    assert ids == {1, 2, 3, 4, 5}
    assert BROADCAST_ID not in ids
    for packet in transport.packets:
        parsed = parse_instruction(packet)
        assert int(parsed["instruction"]) != INST_SYNC_WRITE
        assert int(parsed["id"]) != BROADCAST_ID
        if int(parsed["instruction"]) == 0x03:
            assert int(parsed["address"]) in (ADDR_TORQUE_ENABLE, ADDR_GOAL_POSITION)
    goals = [
        item
        for item in transport.writes
        if int(item["address"]) == ADDR_GOAL_POSITION
    ]
    assert len(goals) == 5
    for item in goals:
        assert abs(int.from_bytes(bytes(item["data"])[:2], "little") - 1000) <= 48
    actuator.apply(walked)
    assert len([item for item in transport.writes if int(item["address"]) == ADDR_GOAL_POSITION]) == 5
    actuator.shutdown()
    assert all(transport.torque[servo_id] == 0 for servo_id in range(1, 6))
    assert 6 not in {int(item["id"]) for item in transport.writes}
