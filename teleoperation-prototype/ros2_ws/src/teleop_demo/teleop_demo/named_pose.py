"""Plan and execute named arm poses through MoveIt move_group.

Pauses Servo so JointTrajectoryController has a single writer, then starts it
again. Does not change /teleop/command.
"""

from __future__ import annotations

import time

from teleop_demo_msgs.srv import GoNamedPose
from moveit_msgs.action import MoveGroup
from moveit_msgs.msg import (
    Constraints,
    JointConstraint,
    MotionPlanRequest,
    MoveItErrorCodes,
    PlanningOptions,
)
import rclpy
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_srvs.srv import Trigger

from teleop_demo.arm_kinematics import ARM_JOINT_NAMES, NAMED_POSES


class NamedPose(Node):
    def __init__(self) -> None:
        super().__init__("named_pose")
        self._group = ReentrantCallbackGroup()
        self._busy = False
        self._move = ActionClient(
            self, MoveGroup, "move_action", callback_group=self._group
        )
        self._stop_servo = self.create_client(
            Trigger, "/servo_node/stop_servo", callback_group=self._group
        )
        self._start_servo = self.create_client(
            Trigger, "/servo_node/start_servo", callback_group=self._group
        )
        self.create_service(
            GoNamedPose,
            "/teleop/go_named_pose",
            self._on_request,
            callback_group=self._group,
        )
        self.get_logger().info(
            "NAMED POSE READY service=/teleop/go_named_pose poses="
            + ",".join(sorted(NAMED_POSES))
        )

    def _on_request(self, request: GoNamedPose.Request, response: GoNamedPose.Response):
        name = request.name.strip().lower()
        if name not in NAMED_POSES:
            response.success = False
            allowed = ", ".join(NAMED_POSES)
            response.message = f"unknown pose '{request.name}'; use {allowed}"
            return response
        if self._busy:
            response.success = False
            response.message = "named pose already executing"
            return response

        self._busy = True
        try:
            self._call_trigger(self._stop_servo, "stop_servo")
            ok, message = self._move_to(name)
            self._call_trigger(self._start_servo, "start_servo")
            response.success = ok
            response.message = message
            return response
        except TimeoutError as exc:
            self._call_trigger(self._start_servo, "start_servo")
            response.success = False
            response.message = f"timed out: {exc}"
            return response
        finally:
            self._busy = False

    def _wait(self, future, timeout_sec: float):
        deadline = time.monotonic() + timeout_sec
        while not future.done():
            if time.monotonic() > deadline:
                raise TimeoutError(f"future not done after {timeout_sec}s")
            time.sleep(0.05)
        return future.result()

    def _call_trigger(self, client, label: str) -> None:
        if not client.wait_for_service(timeout_sec=2.0):
            self.get_logger().warn(f"{label} service not available")
            return
        future = client.call_async(Trigger.Request())
        result = self._wait(future, 5.0)
        if result is None or not result.success:
            self.get_logger().warn(f"{label} unsuccessful: {getattr(result, 'message', '')}")
        else:
            self.get_logger().info(f"{label} ok")

    def _move_to(self, name: str) -> tuple[bool, str]:
        if not self._move.wait_for_server(timeout_sec=10.0):
            return False, "move_action server not available"

        positions = NAMED_POSES[name]
        goal = MoveGroup.Goal()
        request = MotionPlanRequest()
        request.group_name = "arm"
        request.pipeline_id = "ompl"
        request.planner_id = "RRTConnectkConfigDefault"
        request.num_planning_attempts = 5
        request.allowed_planning_time = 8.0
        request.max_velocity_scaling_factor = 0.4
        request.max_acceleration_scaling_factor = 0.4

        constraints = Constraints()
        constraints.name = name
        for joint_name, value in zip(ARM_JOINT_NAMES, positions):
            joint = JointConstraint()
            joint.joint_name = joint_name
            joint.position = float(value)
            joint.tolerance_above = 0.05
            joint.tolerance_below = 0.05
            joint.weight = 1.0
            constraints.joint_constraints.append(joint)
        request.goal_constraints.append(constraints)
        goal.request = request
        goal.planning_options = PlanningOptions()
        goal.planning_options.plan_only = False
        goal.planning_options.replan = True
        goal.planning_options.replan_attempts = 3

        self.get_logger().info(f"NAMED POSE planning name={name}")
        send_future = self._move.send_goal_async(goal)
        handle = self._wait(send_future, 15.0)
        if handle is None or not handle.accepted:
            return False, f"move_group rejected {name}"

        result_future = handle.get_result_async()
        wrapped = self._wait(result_future, 45.0)
        if wrapped is None:
            return False, f"move_group timed out on {name}"
        result = wrapped.result
        error_code = int(result.error_code.val)
        if error_code != MoveItErrorCodes.SUCCESS:
            return False, f"move_group error_code={error_code} for {name}"
        self.get_logger().info(f"NAMED POSE done name={name}")
        return True, f"reached {name}"


def main(args=None) -> None:
    rclpy.init(args=args)
    node = NamedPose()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
