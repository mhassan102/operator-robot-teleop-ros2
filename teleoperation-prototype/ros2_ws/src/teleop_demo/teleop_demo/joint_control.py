"""Step limits for SO-101 follower servos 1-5.

Servo 6 stays on the gripper float in ``gripper_control``. This module
never writes it.

Each joint value is one float, matching ``packaging.operator_app.keys``:

* ``2.0`` and ``1.5`` jog positive while that value keeps arriving
* ``-1.0`` and ``-0.5`` jog negative while it keeps arriving
* ``0.5`` holds
* any other finite value in ``[0, 1]`` is a leader fraction of this
  joint's own travel

A jog takes one step per received command, from the measured present
position, and never more than ``max_delta_ticks`` from the last goal.
The same command does not step again, so a stopped stream does not keep
writing goals while the servo catches up. A leader fraction walks the
same way as the jaw: at most one step per tick toward that fraction of
the joint's limits, then holds. A value outside those tokens and outside
``[0, 1]`` does not move the joint.

A joint with no limits, or whose present position is outside those
limits before the first goal, does not move. Deadman clears every goal
and does not write one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from teleop_demo.gripper_control import (
    CONNECTED,
    DEFAULT_MAX_DELTA_TICKS,
    SAFE_STOP,
    TIMEOUT,
    WATCHDOG_OK,
    clamp_tiny_delta,
)

# Wire values. packaging.operator_app.keys uses the same numbers.
JOINT_HOLD = 0.5
JOG_POSITIVE = (2.0, 1.5)
JOG_NEGATIVE = (-1.0, -0.5)
_TOKEN_TOLERANCE = 1e-4
ARM_COUNT = 5


@dataclass(frozen=True)
class JointGoal:
    servo_id: int
    torque_enable: bool
    goal_position: int | None
    present: int | None


@dataclass(frozen=True)
class ArmOutput:
    deadman: bool
    reason: str
    joints: tuple[JointGoal, ...]


def joint_motion(value: float) -> int | None:
    """+1 or -1 while a jog token is present, 0 to hold, None for a fraction."""
    number = float(value)
    if any(abs(number - token) <= _TOKEN_TOLERANCE for token in JOG_POSITIVE):
        return 1
    if any(abs(number - token) <= _TOKEN_TOLERANCE for token in JOG_NEGATIVE):
        return -1
    if abs(number - JOINT_HOLD) <= _TOKEN_TOLERANCE:
        return 0
    return None


def map_fraction_to_ticks(value: float, low: int, high: int) -> int:
    clamped = max(0.0, min(1.0, float(value)))
    span = int(high) - int(low)
    return int(round(int(low) + clamped * span))


def _inside(present: int, low: int, high: int) -> bool:
    return int(low) <= int(present) <= int(high)


def _clamp(goal: int, low: int, high: int) -> int:
    return max(int(low), min(int(high), int(goal)))


def goal_is_one_step(
    goal: int,
    present: int | None,
    last: int | None,
    max_delta: int,
) -> bool:
    """True when the goal is within one step of the present position or the last goal."""
    anchors = []
    if present is not None:
        anchors.append(int(present))
    if last is not None:
        anchors.append(int(last))
    if not anchors:
        return False
    return any(abs(int(goal) - anchor) <= int(max_delta) for anchor in anchors)


class JointArmController:
    """Turn five joint floats and /teleop/state into torque and goals."""

    def __init__(
        self,
        max_delta_ticks: int = DEFAULT_MAX_DELTA_TICKS,
        command_timeout_s: float = 0.5,
    ) -> None:
        if max_delta_ticks <= 0:
            raise ValueError("max_delta_ticks must be positive")
        self.max_delta_ticks = int(max_delta_ticks)
        self.command_timeout_s = float(command_timeout_s)
        self._limits: list[tuple[int, int] | None] = [None] * ARM_COUNT
        self._goal: list[int | None] = [None] * ARM_COUNT
        self._output: list[int | None] = [None] * ARM_COUNT
        self._values = [JOINT_HOLD] * ARM_COUNT
        self._last_cmd_s: float | None = None
        self._command_gen = 0
        self._stepped_gen = [-1] * ARM_COUNT
        self._seen_state = False
        self._connection_state = TIMEOUT
        self._watchdog_state = SAFE_STOP
        self._live = False

    def set_limits(self, index: int, low: int | None, high: int | None) -> None:
        """Remember one joint's travel. An unknown or empty range never moves."""
        slot = int(index)
        if slot < 0 or slot >= ARM_COUNT:
            raise ValueError(f"joint index {index} is outside 0..{ARM_COUNT - 1}")
        if low is None or high is None or int(high) <= int(low):
            self._limits[slot] = None
            self._goal[slot] = None
            self._output[slot] = None
            self._stepped_gen[slot] = -1
            return
        self._limits[slot] = (int(low), int(high))

    def limits_known(self, index: int) -> bool:
        return self._limits[int(index)] is not None

    def on_command(self, values: list[float] | tuple[float, ...], now_s: float) -> None:
        self._last_cmd_s = float(now_s)
        self._command_gen += 1
        for index in range(ARM_COUNT):
            if index < len(values):
                self._values[index] = float(values[index])
            else:
                self._values[index] = JOINT_HOLD

    def on_teleop_state(self, connection_state: str, watchdog_state: str) -> None:
        self._seen_state = True
        self._connection_state = connection_state
        self._watchdog_state = watchdog_state

    def tick(self, now_s: float, presents: list[int | None]) -> ArmOutput:
        reason = self._deadman_reason(now_s)
        if reason is not None:
            self._goal = [None] * ARM_COUNT
            self._output = [None] * ARM_COUNT
            self._stepped_gen = [-1] * ARM_COUNT
            self._live = False
            return ArmOutput(deadman=True, reason=reason, joints=self._goals(presents))
        self._live = True
        for index in range(ARM_COUNT):
            present = presents[index] if index < len(presents) else None
            self._step_one(index, present)
        return ArmOutput(deadman=False, reason="live", joints=self._goals(presents))

    def _step_one(self, index: int, present: int | None) -> None:
        limits = self._limits[index]
        if limits is None:
            self._goal[index] = None
            self._output[index] = None
            return
        low, high = limits
        value = self._values[index]
        sign = joint_motion(value)
        if sign is None:
            if not math.isfinite(value) or value < -1e-4 or value > 1.0 + 1e-4:
                self._output[index] = self._goal[index]
                return
            self._track_fraction(index, present, low, high, value)
            return
        if sign == 0:
            self._hold(index, present, low, high)
            return
        if present is None or not _inside(present, low, high):
            self._output[index] = self._goal[index]
            return
        # One step for this datagram. Later ticks keep the goal until the
        # next command, including after the packets stop.
        if self._stepped_gen[index] == self._command_gen:
            self._output[index] = self._goal[index]
            return
        stepped = _clamp(int(present) + sign * self.max_delta_ticks, low, high)
        if self._goal[index] is not None:
            stepped = _clamp(
                clamp_tiny_delta(stepped, int(self._goal[index]), self.max_delta_ticks),
                low,
                high,
            )
        self._goal[index] = stepped
        self._output[index] = stepped
        self._stepped_gen[index] = self._command_gen

    def _hold(self, index: int, present: int | None, low: int, high: int) -> None:
        if self._goal[index] is None and present is not None and _inside(present, low, high):
            self._goal[index] = int(present)
        self._output[index] = self._goal[index]

    def _track_fraction(
        self,
        index: int,
        present: int | None,
        low: int,
        high: int,
        value: float,
    ) -> None:
        if not math.isfinite(value):
            self._output[index] = self._goal[index]
            return
        if self._goal[index] is None:
            if present is None or not _inside(present, low, high):
                self._output[index] = None
                return
            base = int(present)
        else:
            base = int(self._goal[index])
        target = _clamp(map_fraction_to_ticks(value, low, high), low, high)
        stepped = _clamp(clamp_tiny_delta(target, base, self.max_delta_ticks), low, high)
        self._goal[index] = stepped
        self._output[index] = stepped

    def _goals(self, presents: list[int | None]) -> tuple[JointGoal, ...]:
        goals = []
        for index in range(ARM_COUNT):
            present = presents[index] if index < len(presents) else None
            goal = self._output[index]
            known = self._limits[index] is not None
            goals.append(
                JointGoal(
                    servo_id=index + 1,
                    torque_enable=bool(known and goal is not None),
                    goal_position=goal if known else None,
                    present=present,
                )
            )
        return tuple(goals)

    def _deadman_reason(self, now_s: float) -> str | None:
        if self._last_cmd_s is None:
            return "no joint command yet"
        if now_s - self._last_cmd_s > self.command_timeout_s:
            return "no joint command (watchdog)"
        if any(not math.isfinite(value) for value in self._values):
            return "non-finite joint command"
        if not self._seen_state:
            return "no /teleop/state yet"
        if self._watchdog_state != WATCHDOG_OK:
            return f"deadman {self._watchdog_state or SAFE_STOP}"
        if self._connection_state != CONNECTED:
            return f"deadman {self._connection_state or TIMEOUT}"
        return None


class JointActuator:
    """Apply arm goals one servo at a time. Deadman writes torque-off, never a goal."""

    def __init__(self, bus, max_delta_ticks: int = DEFAULT_MAX_DELTA_TICKS) -> None:
        self.bus = bus
        self.max_delta_ticks = int(max_delta_ticks)
        self.last_torque: dict[int, bool] = {}
        self.last_goal: dict[int, int | None] = {}

    def apply(self, output: ArmOutput) -> None:
        if output.deadman:
            for servo_id, enabled in list(self.last_torque.items()):
                if enabled is not False:
                    self.bus.write_torque_enable(False, servo_id)
                self.last_torque[servo_id] = False
                self.last_goal[servo_id] = None
            return
        for joint in output.joints:
            self._apply_one(joint)

    def _apply_one(self, joint: JointGoal) -> None:
        servo_id = int(joint.servo_id)
        if servo_id < 1 or servo_id > ARM_COUNT:
            return
        if not joint.torque_enable or joint.goal_position is None:
            if self.last_torque.get(servo_id) is True:
                self.bus.write_torque_enable(False, servo_id)
            self.last_torque[servo_id] = False
            self.last_goal[servo_id] = None
            return
        goal = int(joint.goal_position)
        last = self.last_goal.get(servo_id)
        if not goal_is_one_step(goal, joint.present, last, self.max_delta_ticks):
            return
        if self.last_torque.get(servo_id) is not True:
            self.bus.write_torque_enable(True, servo_id)
            self.last_torque[servo_id] = True
        if goal != last:
            self.bus.write_goal_position(goal, servo_id)
            self.last_goal[servo_id] = goal

    def shutdown(self) -> None:
        for servo_id, enabled in list(self.last_torque.items()):
            if enabled is not False:
                try:
                    self.bus.write_torque_enable(False, servo_id)
                except Exception:  # noqa: BLE001
                    pass
            self.last_torque[servo_id] = False
            self.last_goal[servo_id] = None
