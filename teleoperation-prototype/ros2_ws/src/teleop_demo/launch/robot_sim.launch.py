import os
import sys

import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    LogInfo,
    RegisterEventHandler,
    SetEnvironmentVariable,
    TimerAction,
)
from launch.event_handlers import OnProcessExit
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command, FindExecutable, LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare

from teleop_demo.arm_mode import (
    ARM_REAL,
    InvalidArmMode,
    REAL_ARM_GRIPPER_LOG,
    REAL_ARM_SERIAL_PORT,
    parse_teleop_arm,
)
from teleop_demo.gripper_control import (
    DEFAULT_MAX_DELTA_TICKS,
    VERIFIED_GRIPPER_ID,
    VERIFIED_RANGE_MAX,
    VERIFIED_RANGE_MIN,
)


def _load_yaml(path: str):
    with open(path, "r", encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def _load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as stream:
        return stream.read()


def _teleop_mlink_enabled() -> bool:
    return os.environ.get("TELEOP_MLINK", "").strip() in ("1", "true", "yes", "on")


def _receiver_node() -> Node:
    return Node(
        package="teleop_demo",
        executable="robot_receiver",
        output="screen",
        parameters=["/teleop/config/teleop.yaml"],
    )


def _mlink_bridge_node() -> Node:
    return Node(
        package="teleop_demo",
        executable="robot_mlink_bridge",
        output="screen",
        parameters=["/teleop/config/teleop.yaml"],
    )


def _gripper_node() -> Node:
    serial_port = os.environ.get("TELEOP_SERIAL_PORT", REAL_ARM_SERIAL_PORT)
    return Node(
        package="teleop_demo",
        executable="feetech_gripper",
        output="screen",
        parameters=[
            "/teleop/config/teleop.yaml",
            {
                "serial_port": serial_port,
                "baudrate": 1_000_000,
                "gripper_id": VERIFIED_GRIPPER_ID,
                "range_min": VERIFIED_RANGE_MIN,
                "range_max": VERIFIED_RANGE_MAX,
                "max_delta_ticks": DEFAULT_MAX_DELTA_TICKS,
            },
        ],
    )


def _real_arm_launch_actions() -> list:
    # Stage 2: receiver + Feetech gripper. No Gazebo, Servo, named poses, or
    # arm-joint motion. Serial is opened by feetech_gripper only.
    actions = [
        LogInfo(msg=REAL_ARM_GRIPPER_LOG),
        _receiver_node(),
        _gripper_node(),
    ]
    if _teleop_mlink_enabled():
        actions.append(_mlink_bridge_node())
    return actions


def _gazebo_launch_actions() -> list:
    gui = LaunchConfiguration("gui")
    pkg_share = get_package_share_directory("teleop_demo")
    world = os.path.join(pkg_share, "worlds", "teleop.world")
    controller_yaml = os.path.join(pkg_share, "config", "arm_controllers.yaml")
    xacro_file = os.path.join(pkg_share, "urdf", "teleop_arm.urdf.xacro")
    teleop_yaml = "/teleop/config/teleop.yaml"
    srdf_file = os.path.join(pkg_share, "config", "teleop_arm.srdf")
    kinematics = _load_yaml(os.path.join(pkg_share, "config", "kinematics.yaml"))
    joint_limits = _load_yaml(os.path.join(pkg_share, "config", "joint_limits.yaml"))
    ompl_yaml = _load_yaml(os.path.join(pkg_share, "config", "ompl_planning.yaml"))
    moveit_controllers = _load_yaml(os.path.join(pkg_share, "config", "moveit_controllers.yaml"))
    servo_yaml = _load_yaml(os.path.join(pkg_share, "config", "servo.yaml"))
    robot_description_semantic = _load_text(srdf_file)

    robot_description = ParameterValue(
        Command(
            [
                FindExecutable(name="xacro"),
                " ",
                xacro_file,
                " ",
                "controller_yaml:=",
                controller_yaml,
            ]
        ),
        value_type=str,
    )

    ompl_pipeline = {
        "planning_plugin": "ompl_interface/OMPLPlanner",
        "request_adapters": (
            "default_planner_request_adapters/AddTimeOptimalParameterization "
            "default_planner_request_adapters/ResolveConstraintFrames "
            "default_planner_request_adapters/FixWorkspaceBounds "
            "default_planner_request_adapters/FixStartStateBounds "
            "default_planner_request_adapters/FixStartStateCollision "
            "default_planner_request_adapters/FixStartStatePathConstraints"
        ),
        "start_state_max_bounds_error": 0.1,
    }
    ompl_pipeline.update(ompl_yaml or {})

    moveit_params = [
        {"robot_description": robot_description},
        {"robot_description_semantic": robot_description_semantic},
        {"robot_description_kinematics": kinematics},
        {"robot_description_planning": joint_limits},
        {
            "planning_pipelines": ["ompl"],
            "default_planning_pipeline": "ompl",
            "ompl": ompl_pipeline,
        },
        moveit_controllers,
        {
            "use_sim_time": True,
            "publish_robot_description_semantic": True,
        },
    ]

    gazebo = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            [
                PathJoinSubstitution(
                    [FindPackageShare("gazebo_ros"), "launch", "gazebo.launch.py"]
                )
            ]
        ),
        launch_arguments={
            "world": world,
            "gui": gui,
            "paused": "false",
            "verbose": "false",
        }.items(),
    )

    rsp = Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        parameters=[{"robot_description": robot_description, "use_sim_time": True}],
        output="screen",
    )

    spawn = Node(
        package="gazebo_ros",
        executable="spawn_entity.py",
        arguments=["-topic", "robot_description", "-entity", "teleop_arm", "-z", "0.0"],
        output="screen",
    )

    jsb = Node(
        package="controller_manager",
        executable="spawner",
        arguments=["joint_state_broadcaster"],
        output="screen",
    )
    arm_spawner = Node(
        package="controller_manager",
        executable="spawner",
        arguments=["arm_controller"],
        output="screen",
    )
    gripper_spawner = Node(
        package="controller_manager",
        executable="spawner",
        arguments=["gripper_controller"],
        output="screen",
    )

    receiver = _receiver_node()

    move_group = Node(
        package="moveit_ros_move_group",
        executable="move_group",
        output="screen",
        parameters=moveit_params,
    )

    servo_node = Node(
        package="moveit_servo",
        executable="servo_node_main",
        name="servo_node",
        output="screen",
        parameters=[
            {"moveit_servo": servo_yaml},
            {"robot_description": robot_description},
            {"robot_description_semantic": robot_description_semantic},
            {"robot_description_kinematics": kinematics},
            {"use_sim_time": True},
        ],
    )

    servo_bridge = Node(
        package="teleop_demo",
        executable="servo_bridge",
        output="screen",
        parameters=[teleop_yaml, {"use_sim_time": True}],
    )
    named_pose = Node(
        package="teleop_demo",
        executable="named_pose",
        output="screen",
        parameters=[{"use_sim_time": True}],
    )
    mlink_bridge = _mlink_bridge_node()

    delayed_spawn = TimerAction(period=4.0, actions=[spawn])
    after_spawn = RegisterEventHandler(
        OnProcessExit(
            target_action=spawn,
            on_exit=[jsb, arm_spawner, gripper_spawner],
        )
    )

    actions = [
        DeclareLaunchArgument(
            "gui",
            default_value=os.environ.get("TELEOP_GAZEBO_GUI", "false"),
        ),
        SetEnvironmentVariable("LIBGL_ALWAYS_SOFTWARE", "1"),
        gazebo,
        rsp,
        receiver,
        delayed_spawn,
        after_spawn,
        TimerAction(
            period=10.0,
            actions=[move_group, servo_node, servo_bridge, named_pose],
        ),
    ]
    if _teleop_mlink_enabled():
        actions.append(mlink_bridge)
    return actions


def generate_launch_description() -> LaunchDescription:
    try:
        arm_mode = parse_teleop_arm(os.environ.get("TELEOP_ARM"))
    except InvalidArmMode as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise RuntimeError(str(exc)) from exc
    if arm_mode == ARM_REAL:
        return LaunchDescription(_real_arm_launch_actions())
    return LaunchDescription(_gazebo_launch_actions())
