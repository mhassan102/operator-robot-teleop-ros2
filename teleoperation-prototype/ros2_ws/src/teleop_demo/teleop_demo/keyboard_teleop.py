"""Keyboard teleop: hold keys to stream the same TeleopCommand Twist as CLI bursts."""

from __future__ import annotations

import select
import sys
import termios
import threading
import time
import tty
import uuid

from teleop_demo_msgs.msg import TeleopCommand, TeleopHeartbeat
import rclpy
from rclpy.node import Node

from teleop_demo.commands import COMMANDS, KEY_BINDINGS, fill_twist
from teleop_demo.parameters import declare_teleop_parameters
from teleop_demo.qos import command_qos

HELP = """
Keyboard teleop (focus this terminal)
  w/s  +x / x-      r/f  +z / z-
  a/d  +y / y-      j/l  +yaw / yaw-
  u/o  +roll        i/k  +pitch
  g/h  open/close   space  stop
  Ctrl-C  quit

Hold a key (repeat) to keep jogging. Release -> zero Twist after 0.3 s.
Heartbeat stays on while this node runs.
"""


class KeyboardTeleop(Node):
    def __init__(self) -> None:
        super().__init__("keyboard_teleop")
        declare_teleop_parameters(self)
        self.session_id = uuid.uuid4().hex[:12]
        self.frame_id = str(self.get_parameter("command_frame").value)
        self.command_sequence = 0
        self.heartbeat_sequence = 0
        self._lock = threading.Lock()
        self._motion = COMMANDS["stop"][:6]
        self._gripper = 0.0
        self._label = "stop"
        self._last_key_s = 0.0
        qos = command_qos()
        self.command_pub = self.create_publisher(TeleopCommand, "/teleop/command", qos)
        self.heartbeat_pub = self.create_publisher(TeleopHeartbeat, "/teleop/heartbeat", qos)
        command_rate = float(self.get_parameter("command_rate_hz").value)
        heartbeat_rate = float(self.get_parameter("heartbeat_rate_hz").value)
        self.create_timer(1.0 / command_rate, self._publish_command)
        self.create_timer(1.0 / heartbeat_rate, self._publish_heartbeat)
        self.get_logger().info(
            f"KEYBOARD TELEOP session={self.session_id} {HELP}"
        )

    def apply_key(self, key: str) -> None:
        direction = KEY_BINDINGS.get(key)
        if direction is None:
            return
        values = COMMANDS[direction]
        with self._lock:
            self._last_key_s = time.monotonic()
            if direction in ("open", "close"):
                self._gripper = values[6]
            elif direction == "stop":
                self._motion = values[:6]
            else:
                self._motion = values[:6]
            self._label = direction
        self.get_logger().info(f"KEY {direction}")

    def _current(self) -> tuple[tuple[float, ...], float, str]:
        with self._lock:
            if self._last_key_s > 0.0 and time.monotonic() - self._last_key_s > 0.3:
                if self._label not in ("stop", "open", "close"):
                    self._motion = COMMANDS["stop"][:6]
                    self._label = "stop"
            return self._motion, self._gripper, self._label

    def _publish_command(self) -> None:
        motion, gripper, label = self._current()
        self.command_sequence += 1
        message = TeleopCommand()
        message.sequence = self.command_sequence
        message.stamp = self.get_clock().now().to_msg()
        message.frame_id = self.frame_id
        message.session_id = self.session_id
        fill_twist(message.twist, motion)
        message.gripper = gripper
        self.command_pub.publish(message)

    def _publish_heartbeat(self) -> None:
        self.heartbeat_sequence += 1
        message = TeleopHeartbeat()
        message.sequence = self.heartbeat_sequence
        message.stamp = self.get_clock().now().to_msg()
        message.session_id = self.session_id
        self.heartbeat_pub.publish(message)


def _read_keys(node: KeyboardTeleop, stop: threading.Event) -> None:
    while not stop.is_set() and rclpy.ok():
        ready, _unused, _unused2 = select.select([sys.stdin], [], [], 0.1)
        if not ready:
            continue
        key = sys.stdin.read(1)
        if key == "\x03":
            rclpy.shutdown()
            break
        node.apply_key(key)


def main(args=None) -> None:
    if not sys.stdin.isatty():
        raise SystemExit(
            "keyboard_teleop needs a real terminal. Use:\n"
            "  ./scripts/keyboard_teleop.sh"
        )
    settings = termios.tcgetattr(sys.stdin)
    rclpy.init(args=args)
    node = KeyboardTeleop()
    stop = threading.Event()
    reader = threading.Thread(target=_read_keys, args=(node, stop), daemon=True)
    try:
        tty.setcbreak(sys.stdin.fileno())
        reader.start()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
