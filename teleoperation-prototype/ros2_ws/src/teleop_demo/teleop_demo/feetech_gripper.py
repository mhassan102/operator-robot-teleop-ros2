"""ROS 2 node: follower SO-101 on one Feetech bus (TELEOP_ARM=real only).

Servo 6 follows /gripper_safe. Servos 1-5 follow /teleop/joint_command,
one step at a time, and only when that joint's limits were read.
Missing/busy serial exits without starting Gazebo.
"""

from __future__ import annotations

import sys
import time

from std_msgs.msg import Float64, Float64MultiArray, Int32MultiArray
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
from teleop_demo.feetech_protocol import (
    ADDR_MAX_POSITION,
    ADDR_MIN_POSITION,
    ForbiddenServoCommand,
)
from teleop_demo.gripper_control import (
    DEFAULT_MAX_DELTA_TICKS,
    VERIFIED_GRIPPER_ID,
    VERIFIED_RANGE_MAX,
    VERIFIED_RANGE_MIN,
    GripperActuator,
    GripperController,
)
from teleop_demo.joint_control import JointActuator, JointArmController
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
        max_delta = int(self.get_parameter("max_delta_ticks").value)
        self.controller = GripperController(
            range_min=int(self.get_parameter("range_min").value),
            range_max=int(self.get_parameter("range_max").value),
            max_delta_ticks=max_delta,
            command_timeout_s=watchdog_s,
        )
        self.arm = JointArmController(
            max_delta_ticks=max_delta,
            command_timeout_s=watchdog_s,
        )
        self._last_deadman: bool | None = None
        self._arm_deadman: bool | None = None
        self._present = 0
        self._joint_ticks = [-1, -1, -1, -1, -1, -1]
        self._joint_cursor = 0
        self.bus = bus if bus is not None else self._open_bus()
        self.actuator = GripperActuator(self.bus)
        self.arm_actuator = JointActuator(self.bus, max_delta_ticks=max_delta)
        self._present = self._connect()

        qos = command_qos()
        self._joint_pub = self.create_publisher(
            Int32MultiArray, "/teleop/joint_ticks", qos
        )
        self.create_subscription(Float64, "/gripper_safe", self._on_gripper, qos)
        self.create_subscription(
            Float64MultiArray, "/teleop/joint_command", self._on_joint_command, qos
        )
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
        self._read_arm_limits()
        return present

    def _read_arm_limits(self) -> None:
        """One min/max read per arm joint. A miss leaves that joint unmoved."""
        for index in range(5):
            servo_id = index + 1
            try:
                low = self.bus.read_register(servo_id, ADDR_MIN_POSITION)
                high = self.bus.read_register(servo_id, ADDR_MAX_POSITION)
                present = self.bus.read_present_position(servo_id)
            except (FeetechBusError, ForbiddenServoCommand, OSError) as exc:
                self.arm.set_limits(index, None, None)
                self.get_logger().info(
                    f"JOINT LIMITS id={servo_id} unknown ({exc}); not moving"
                )
                continue
            if high <= low:
                self.arm.set_limits(index, None, None)
                self.get_logger().info(
                    f"JOINT LIMITS id={servo_id} {low}..{high} unusable; not moving"
                )
                continue
            self.arm.set_limits(index, low, high)
            if 0 <= int(present) <= 32767:
                self._joint_ticks[index] = int(present)
            self.get_logger().info(
                f"JOINT LIMITS id={servo_id} {low}..{high} present={present}"
            )

    def _on_gripper(self, message: Float64) -> None:
        self.controller.on_gripper_safe(float(message.data), time.monotonic())

    def _on_joint_command(self, message: Float64MultiArray) -> None:
        values = [float(item) for item in message.data]
        self.arm.on_command(values[:5], time.monotonic())

    def _on_state(self, message: TeleopState) -> None:
        connection = str(message.connection_state)
        watchdog = str(message.watchdog_state)
        self.controller.on_teleop_state(connection, watchdog)
        self.arm.on_teleop_state(connection, watchdog)

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
        arm = self.arm.tick(now, self._arm_presents())
        if arm.deadman:
            if self._arm_deadman is not True:
                self.get_logger().info(f"ARM DEADMAN torque=off ({arm.reason})")
            self._arm_deadman = True
        elif self._arm_deadman is not False:
            self.get_logger().info("ARM LIVE")
            self._arm_deadman = False
        self.arm_actuator.apply(arm)
        self._sample_one_joint()

    def _arm_presents(self) -> list[int | None]:
        presents: list[int | None] = []
        for value in self._joint_ticks[:5]:
            if value < 0:
                presents.append(None)
            else:
                presents.append(int(value))
        return presents

    def _sample_one_joint(self) -> None:
        """One present-position read per tick. A miss keeps the last tick."""
        index = self._joint_cursor
        self._joint_cursor = (index + 1) % 6
        try:
            tick = self.bus.read_present_position(index + 1)
        except (FeetechBusError, ForbiddenServoCommand, OSError):
            return
        if tick < 0 or tick > 32767:
            return
        self._joint_ticks[index] = int(tick)
        message = Int32MultiArray()
        message.data = list(self._joint_ticks)
        self._joint_pub.publish(message)

    def destroy_node(self) -> bool:
        try:
            self.arm_actuator.shutdown()
        except Exception:  # noqa: BLE001
            pass
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
