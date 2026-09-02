import os

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


def generate_launch_description() -> LaunchDescription:
    gui = LaunchConfiguration("gui")
    pkg_share = get_package_share_directory("teleop_demo")
    world = os.path.join(pkg_share, "worlds", "teleop.world")
    controller_yaml = os.path.join(pkg_share, "config", "arm_controllers.yaml")
    xacro_file = os.path.join(pkg_share, "urdf", "teleop_arm.urdf.xacro")
    teleop_yaml = "/teleop/config/teleop.yaml"

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
    jog = Node(
        package="teleop_demo",
        executable="cartesian_jog",
        output="screen",
        parameters=[teleop_yaml, {"use_sim_time": True}],
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
            TimerAction(period=8.0, actions=[jog]),
        ]
    )
