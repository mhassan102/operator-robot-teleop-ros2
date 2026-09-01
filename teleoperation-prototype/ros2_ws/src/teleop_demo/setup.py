from setuptools import find_packages, setup


package_name = "teleop_demo"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
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
            "robot_receiver = teleop_demo.robot_receiver:main",
        ],
    },
)
