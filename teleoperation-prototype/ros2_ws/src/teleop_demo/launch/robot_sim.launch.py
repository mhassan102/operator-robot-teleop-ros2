import os

import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
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


def _load_yaml(path: str):
    with open(path, "r", encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def _load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as stream:
        return stream.read()


def generate_launch_description() -> LaunchDescription:
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

    receiver = Node(
        package="teleop_demo",
        executable="robot_receiver",
        output="screen",
        parameters=[teleop_yaml],
    )

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

    delayed_spawn = TimerAction(period=4.0, actions=[spawn])
    after_spawn = RegisterEventHandler(
        OnProcessExit(
            target_action=spawn,
            on_exit=[jsb, arm_spawner, gripper_spawner],
        )
    )

    return LaunchDescription(
        [
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
    )
