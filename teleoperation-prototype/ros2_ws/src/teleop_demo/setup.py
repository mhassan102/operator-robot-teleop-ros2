import os
from glob import glob

from setuptools import find_packages, setup


package_name = "teleop_demo"


def share_files(subdir: str) -> tuple[str, list[str]]:
    return os.path.join("share", package_name, subdir), glob(os.path.join(subdir, "*"))


setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        share_files("launch"),
        share_files("urdf"),
        share_files("worlds"),
        share_files("config"),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Teleoperation POC",
    maintainer_email="developer@example.invalid",
    description="Containerized ROS 2 teleoperation proof-of-concept nodes.",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "operator_command = teleop_demo.operator_command:main",
            "operator_heartbeat = teleop_demo.operator_heartbeat:main",
            "keyboard_teleop = teleop_demo.keyboard_teleop:main",
            "robot_receiver = teleop_demo.robot_receiver:main",
            "cartesian_jog = teleop_demo.cartesian_jog:main",
        ],
    },
)
