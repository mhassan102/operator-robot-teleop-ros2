"""Robot-side safety gateway: validate, clamp, heartbeat watchdog, safe stop.

Connection policy (watchdog_keep_alive):
- command_or_heartbeat: either a valid command or a heartbeat resets the
  watchdog. This is the default so scripted bursts without a heartbeat node
  still stay CONNECTED.
- heartbeat_only: only heartbeats reset the watchdog. Commands can move the
  arm only while the operator liveness topic is alive.

Cartesian jog is a rate. If commands go silent while still CONNECTED, the safe
twist is zeroed after command_timeout_s. The last non-zero twist is never
replayed after a watchdog timeout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from geometry_msgs.msg import Twist

from teleop_demo.commands import (
    clamp_gripper,
    clamp_twist,
    twist_is_finite,
    zero_twist,
)

CONNECTED = "CONNECTED"
TIMEOUT = "TIMEOUT"
SAFE_STOP = "SAFE STOP ACTIVATED"
RESTORED = "RESTORED"

WATCHDOG_OK = "OK"
DISPOSITION_ACCEPTED = "ACCEPTED"
DISPOSITION_CLAMPED = "CLAMPED"
DISPOSITION_REJECTED = "REJECTED"
DISPOSITION_HELD = "HELD"
DISPOSITION_ZEROED = "ZEROED"

KEEP_ALIVE_COMMAND_OR_HEARTBEAT = "command_or_heartbeat"
KEEP_ALIVE_HEARTBEAT_ONLY = "heartbeat_only"


def _copy_twist(message: Twist) -> Twist:
    copied = Twist()
    copied.linear.x = message.linear.x
    copied.linear.y = message.linear.y
    copied.linear.z = message.linear.z
    copied.angular.x = message.angular.x
    copied.angular.y = message.angular.y
    copied.angular.z = message.angular.z
    return copied


@dataclass
class SafetyOutput:
    twist: Twist
    gripper: float
    frame_id: str
    connection_state: str
    watchdog_state: str
    last_disposition: str
    transitions: list[str] = field(default_factory=list)


class SafetyController:
    """Monotonic-time safety state machine. ROS-free for unit tests."""

    def __init__(
        self,
        watchdog_timeout_s: float,
        command_timeout_s: float,
        max_linear: float,
        max_angular: float,
        min_gripper: float,
        max_gripper: float,
        default_frame: str,
        keep_alive: str = KEEP_ALIVE_COMMAND_OR_HEARTBEAT,
    ) -> None:
        self.watchdog_timeout_s = watchdog_timeout_s
        self.command_timeout_s = command_timeout_s
        self.max_linear = max_linear
        self.max_angular = max_angular
        self.min_gripper = min_gripper
        self.max_gripper = max_gripper
        self.default_frame = default_frame
        self.keep_alive = keep_alive
        self.connection_state = TIMEOUT
        self.watchdog_state = SAFE_STOP
        self.last_disposition = DISPOSITION_ZEROED
        self.frame_id = default_frame
        self._ever_connected = False
        self._awaiting_fresh_command = True
        self._last_keep_alive_s: Optional[float] = None
        self._last_command_s: Optional[float] = None
        self._active_twist = zero_twist()
        self._output_twist = zero_twist()
        self._gripper = min_gripper
        self._pending_startup_stop = True

    def on_heartbeat(self, now_s: float) -> list[str]:
        return self._register_keep_alive(now_s, source="heartbeat")

    def on_command(
        self, twist: Twist, gripper: float, frame_id: str, now_s: float
    ) -> tuple[str, list[str]]:
        if not twist_is_finite(twist) or not _is_finite(gripper):
            self.last_disposition = DISPOSITION_REJECTED
            return DISPOSITION_REJECTED, []

        working = _copy_twist(twist)
        was_clamped = clamp_twist(working, self.max_linear, self.max_angular)
        gripper = clamp_gripper(gripper, self.min_gripper, self.max_gripper)
        if frame_id:
            self.frame_id = frame_id

        transitions: list[str] = []
        if self.keep_alive != KEEP_ALIVE_HEARTBEAT_ONLY:
            transitions.extend(self._register_keep_alive(now_s, source="command"))

        if self.connection_state == TIMEOUT:
            self.last_disposition = DISPOSITION_HELD
            return DISPOSITION_HELD, transitions

        if self._awaiting_fresh_command:
            if self.connection_state == RESTORED:
                self.connection_state = CONNECTED
                transitions.append(CONNECTED)
            self._awaiting_fresh_command = False
            self.watchdog_state = WATCHDOG_OK

        self._active_twist = working
        self._output_twist = _copy_twist(working)
        self._gripper = gripper
        self._last_command_s = now_s
        disposition = DISPOSITION_CLAMPED if was_clamped else DISPOSITION_ACCEPTED
        self.last_disposition = disposition
        return disposition, transitions

    def tick(self, now_s: float) -> SafetyOutput:
        transitions: list[str] = []
        if self._pending_startup_stop:
            transitions.append(SAFE_STOP)
            self._pending_startup_stop = False

        if self._last_keep_alive_s is not None:
            elapsed = now_s - self._last_keep_alive_s
            if elapsed > self.watchdog_timeout_s and self.connection_state != TIMEOUT:
                self.connection_state = TIMEOUT
                self.watchdog_state = SAFE_STOP
                self._awaiting_fresh_command = True
                self._active_twist = zero_twist()
                self._output_twist = zero_twist()
                self.last_disposition = DISPOSITION_ZEROED
                transitions.extend([TIMEOUT, SAFE_STOP])

        if self.connection_state == CONNECTED and not self._awaiting_fresh_command:
            if (
                self._last_command_s is None
                or now_s - self._last_command_s > self.command_timeout_s
            ):
                self._output_twist = zero_twist()
            else:
                self._output_twist = _copy_twist(self._active_twist)
        else:
            self._output_twist = zero_twist()

        return SafetyOutput(
            twist=_copy_twist(self._output_twist),
            gripper=self._gripper,
            frame_id=self.frame_id,
            connection_state=self.connection_state,
            watchdog_state=self.watchdog_state,
            last_disposition=self.last_disposition,
            transitions=transitions,
        )

    def _register_keep_alive(self, now_s: float, source: str) -> list[str]:
        transitions: list[str] = []
        self._last_keep_alive_s = now_s
        if self.connection_state == TIMEOUT:
            if not self._ever_connected:
                self.connection_state = CONNECTED
                self.watchdog_state = WATCHDOG_OK
                self._ever_connected = True
                self._awaiting_fresh_command = source != "command"
                transitions.append(CONNECTED)
            else:
                self.connection_state = RESTORED
                self._awaiting_fresh_command = True
                transitions.append(RESTORED)
        return transitions


def _is_finite(value: float) -> bool:
    return math.isfinite(value)
