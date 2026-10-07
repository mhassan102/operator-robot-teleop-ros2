"""ROS 2 node: /gripper_safe -> Feetech STS3215 gripper (TELEOP_ARM=real only).

Writes torque/goal to the verified gripper id only. Joints 1-5 are never
addressed. Missing/busy serial exits without starting Gazebo.
"""

from __future__ import annotations

import sys
import time

from std_msgs.msg import Float64
from teleop_demo_msgs.msg import TeleopState
import rclpy
from rclpy.node import Node

from teleop_demo.arm_mode import serial_port_from_env
from teleop_demo.feetech_bus import (
    FeetechBus,
    FeetechBusError,
    SerialBusyError,
    SerialMissingError,
    SerialTransport,
)
from teleop_demo.gripper_control import (
    DEFAULT_MAX_DELTA_TICKS,
    VERIFIED_GRIPPER_ID,
    VERIFIED_RANGE_MAX,
    VERIFIED_RANGE_MIN,
    GripperActuator,
    GripperController,
)
from teleop_demo.parameters import declare_teleop_parameters
from teleop_demo.qos import command_qos


class FeetechGripper(Node):
    def __init__(self, bus: FeetechBus | None = None) -> None:
        super().__init__("feetech_gripper")
        declare_teleop_parameters(self)
        default_port = serial_port_from_env()
        self.declare_parameter("serial_port", default_port)
        self.declare_parameter("baudrate", 1_000_000)
        self.declare_parameter("gripper_id", VERIFIED_GRIPPER_ID)
        self.declare_parameter("range_min", VERIFIED_RANGE_MIN)
        self.declare_parameter("range_max", VERIFIED_RANGE_MAX)
        self.declare_parameter("max_delta_ticks", DEFAULT_MAX_DELTA_TICKS)

        self.serial_port = str(self.get_parameter("serial_port").value)
        self.baudrate = int(self.get_parameter("baudrate").value)
        self.gripper_id = int(self.get_parameter("gripper_id").value)
        watchdog_s = int(self.get_parameter("watchdog_timeout_ms").value) / 1000.0
        rate = float(self.get_parameter("controller_rate_hz").value)
        self.controller = GripperController(
            range_min=int(self.get_parameter("range_min").value),
            range_max=int(self.get_parameter("range_max").value),
            max_delta_ticks=int(self.get_parameter("max_delta_ticks").value),
            command_timeout_s=watchdog_s,
        )
        self._last_deadman: bool | None = None
        self._present = 0
        self.bus = bus if bus is not None else self._open_bus()
        self.actuator = GripperActuator(self.bus)
        self._present = self._connect()

        qos = command_qos()
        self.create_subscription(Float64, "/gripper_safe", self._on_gripper, qos)
        self.create_subscription(TeleopState, "/teleop/state", self._on_state, qos)
        self.create_timer(1.0 / rate, self._tick)
        self.get_logger().info(
            "FEETECH GRIPPER READY "
            f"port={self.serial_port} id={self.gripper_id} "
            f"present={self._present} "
            f"range={self.controller.range_min}..{self.controller.range_max} "
            f"max_delta={self.controller.max_delta_ticks}"
        )

    def _open_bus(self) -> FeetechBus:
        try:
            transport = SerialTransport.open_exclusive(self.serial_port, self.baudrate)
        except (SerialMissingError, SerialBusyError, FeetechBusError):
            raise
        except OSError as exc:
            raise SerialMissingError(
                f"serial port missing: {self.serial_port} ({exc}); "
                "refusing to fall back to Gazebo"
            ) from exc
        return FeetechBus(transport, gripper_id=self.gripper_id)

    def _connect(self) -> int:
        try:
            self.bus.ping()
            present = self.bus.read_present_position()
            self.bus.write_torque_enable(False)
        except FeetechBusError as exc:
            self.bus.close()
            raise FeetechBusError(
                f"gripper id {self.gripper_id} not found on {self.serial_port}: {exc}; "
                "refusing to fall back to Gazebo"
            ) from exc
        self.actuator.last_torque = False
        return present

    def _on_gripper(self, message: Float64) -> None:
        self.controller.on_gripper_safe(float(message.data), time.monotonic())

    def _on_state(self, message: TeleopState) -> None:
        self.controller.on_teleop_state(
            str(message.connection_state), str(message.watchdog_state)
        )

    def _tick(self) -> None:
        now = time.monotonic()
        # No step goal yet: read the jaw so the next press starts here,
        # including the first press after deadman.
        if not self.controller.is_deadman(now) and self.controller.goal is None:
            self._present = self.bus.read_present_position()
        output = self.controller.tick(now, self._present)
        if output.deadman:
            if self._last_deadman is not True:
                self.get_logger().info(f"GRIPPER DEADMAN torque=off ({output.reason})")
            self._last_deadman = True
        elif self._last_deadman is not False:
            self.get_logger().info(
                f"GRIPPER LIVE torque=on present={self._present} "
                f"goal={output.goal_position}"
            )
            self._last_deadman = False
        self.actuator.apply(output)

    def destroy_node(self) -> bool:
        try:
            self.actuator.shutdown()
        except Exception:  # noqa: BLE001
            pass
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = None
    try:
        node = FeetechGripper()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except (SerialMissingError, SerialBusyError, FeetechBusError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if node is None and rclpy.ok():
            rclpy.shutdown()
        raise SystemExit(1) from exc
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
