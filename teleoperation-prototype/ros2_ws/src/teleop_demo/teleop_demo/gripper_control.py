"""ROS-free SO-ARM gripper policy.

/gripper_safe is still one float. The keyboard tokens are exact: 1.0 and
0.75 request an open step, 0.0 and 0.25 request a close step, and 0.5 holds.
A repeated token is a latch, not another press. The first keyboard sample
after the link goes live is that latch and does not move the jaw.

Any other finite value is a leader jaw fraction, 0 closed to 1 open. Each
tick walks at most 48 ticks toward that opening, including while the same
fraction keeps arriving, and holds once it is there. A leader fraction on
the first live sample starts that walk. Deadman drops torque, clears the
goal, and does not write a close.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from teleop_demo.feetech_protocol import POSITION_MAX, POSITION_MIN

# Must match teleop_demo.safety / TeleopState string values.
CONNECTED = "CONNECTED"
TIMEOUT = "TIMEOUT"
SAFE_STOP = "SAFE STOP ACTIVATED"
WATCHDOG_OK = "OK"


# Verified 2026-09-16 on usama@gt-dev-muhammadusama /dev/ttyACM0.
# LeRobot so_follower calibration gripper: id 6, range_min 2036, range_max 3466.
# Read-only ping: IDs 1-6 present; id 6 present_position=2061. Positions of
# 1-5 match so_follower ranges, not so_leader. Follower arm is plugged in.
VERIFIED_GRIPPER_ID = 6
VERIFIED_RANGE_MIN = 2036
VERIFIED_RANGE_MAX = 3466
DEFAULT_MAX_DELTA_TICKS = 48


@dataclass(frozen=True)
class GripperOutput:
    torque_enable: bool
    goal_position: int | None
    deadman: bool
    reason: str


def map_gripper_to_ticks(
    value: float,
    range_min: int = VERIFIED_RANGE_MIN,
    range_max: int = VERIFIED_RANGE_MAX,
) -> int:
    clamped = max(0.0, min(1.0, float(value)))
    span = range_max - range_min
    return int(round(range_min + clamped * span))


def clamp_tiny_delta(target: int, anchor: int, max_delta: int) -> int:
    lo = anchor - max_delta
    hi = anchor + max_delta
    return max(lo, min(hi, target))


# Bands match packaging.operator_app.keys step tokens.
OPEN_LEVEL = 0.75
CLOSE_LEVEL = 0.25

# Exact floats the operator keyboard sends. Leader fractions avoid these.
# packaging.operator_app.leader.KEYBOARD_TOKENS matches this tuple.
KEYBOARD_TOKENS = (0.0, 0.25, 0.5, 0.75, 1.0)
KEYBOARD_TOKEN_TOLERANCE = 1e-4


def is_keyboard_gripper(value: float) -> bool:
    """True for g/h step tokens and hold. Anything else is a jaw fraction."""
    return any(
        abs(float(value) - token) <= KEYBOARD_TOKEN_TOLERANCE for token in KEYBOARD_TOKENS
    )


def gripper_motion(value: float) -> int:
    """+1 open one step, -1 close one step, 0 hold. Keyboard tokens only."""
    if value >= OPEN_LEVEL:
        return 1
    if value <= CLOSE_LEVEL:
        return -1
    return 0


class GripperController:
    """Turn /gripper_safe + /teleop/state into torque/goal for one servo."""

    def __init__(
        self,
        range_min: int = VERIFIED_RANGE_MIN,
        range_max: int = VERIFIED_RANGE_MAX,
        max_delta_ticks: int = DEFAULT_MAX_DELTA_TICKS,
        command_timeout_s: float = 0.5,
    ) -> None:
        if max_delta_ticks <= 0:
            raise ValueError("max_delta_ticks must be positive")
        if range_max <= range_min:
            raise ValueError("range_max must be greater than range_min")
        self.range_min = int(range_min)
        self.range_max = int(range_max)
        self.max_delta_ticks = int(max_delta_ticks)
        self.command_timeout_s = float(command_timeout_s)
        self._last_safe_s: float | None = None
        self._last_value = 0.0
        self._seen_state = False
        self._connection_state = TIMEOUT
        self._watchdog_state = SAFE_STOP
        self._goal: int | None = None
        self._output: int | None = None
        self._seen: float | None = None
        self._live = False

    def on_gripper_safe(self, value: float, now_s: float) -> None:
        self._last_safe_s = now_s
        self._last_value = float(value)

    def on_teleop_state(self, connection_state: str, watchdog_state: str) -> None:
        self._seen_state = True
        self._connection_state = connection_state
        self._watchdog_state = watchdog_state

    def tick(self, now_s: float, present_position: int) -> GripperOutput:
        reason = self._deadman_reason(now_s)
        if reason is not None:
            self._goal = None
            self._output = None
            self._live = False
            return GripperOutput(
                torque_enable=False,
                goal_position=None,
                deadman=True,
                reason=reason,
            )
        present = int(present_position)
        value = float(self._last_value)
        if not self._live:
            # A keyboard float is adopted so a reconnect or the initial 0.0
            # does not take a step. A leader fraction starts walking now.
            self._live = True
            self._seen = value
            if not is_keyboard_gripper(value):
                self._track_position(present, value)
                return self._live_output()
            self._output = self._clamp_goal(present)
            return self._live_output()
        if not is_keyboard_gripper(value):
            self._track_position(present, value)
            return self._live_output()
        if value != self._seen:
            motion = gripper_motion(value)
            self._seen = value
            if motion != 0:
                base = self._goal if self._goal is not None else present
                self._goal = self._clamp_goal(base + motion * self.max_delta_ticks)
                self._output = self._goal
        return self._live_output()

    def _track_position(self, present: int, value: float) -> None:
        """Walk from the last goal toward this jaw fraction, at most one step."""
        base = self._goal if self._goal is not None else int(present)
        target = self._clamp_goal(
            map_gripper_to_ticks(value, self.range_min, self.range_max)
        )
        stepped = self._clamp_goal(
            clamp_tiny_delta(target, base, self.max_delta_ticks)
        )
        self._goal = stepped
        self._output = stepped
        self._seen = float(value)

    @property
    def goal(self) -> int | None:
        """Last step target. None until a press, including after deadman."""
        return self._goal

    def _live_output(self) -> GripperOutput:
        return GripperOutput(
            torque_enable=True,
            goal_position=self._output,
            deadman=False,
            reason="live",
        )

    def _clamp_goal(self, goal: int) -> int:
        low = max(self.range_min, POSITION_MIN)
        high = min(self.range_max, POSITION_MAX)
        return max(low, min(high, int(goal)))

    def is_deadman(self, now_s: float) -> bool:
        return self._deadman_reason(now_s) is not None

    def _deadman_reason(self, now_s: float) -> str | None:
        if self._last_safe_s is None:
            return "no /gripper_safe yet"
        if now_s - self._last_safe_s > self.command_timeout_s:
            return "no /gripper_safe (watchdog)"
        if not math.isfinite(self._last_value):
            return "non-finite /gripper_safe"
        if not self._seen_state:
            return "no /teleop/state yet"
        if self._watchdog_state != WATCHDOG_OK:
            return f"deadman {self._watchdog_state or SAFE_STOP}"
        if self._connection_state != CONNECTED:
            return f"deadman {self._connection_state or TIMEOUT}"
        return None


class GripperActuator:
    """Apply controller output to the bus. Deadman writes torque-off, never a close."""

    def __init__(self, bus) -> None:
        self.bus = bus
        self.last_torque: bool | None = None
        self.last_goal: int | None = None

    def apply(self, output: GripperOutput) -> None:
        if output.deadman:
            if self.last_torque is not False:
                self.bus.write_torque_enable(False)
                self.last_torque = False
                self.last_goal = None
            return
        if self.last_torque is not True:
            self.bus.write_torque_enable(True)
            self.last_torque = True
        if output.goal_position is None:
            return
        if output.goal_position != self.last_goal:
            self.bus.write_goal_position(output.goal_position)
            self.last_goal = output.goal_position

    def shutdown(self) -> None:
        try:
            self.bus.write_torque_enable(False)
        finally:
            self.last_torque = False
            self.last_goal = None
            self.bus.close()
