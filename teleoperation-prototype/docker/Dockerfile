FROM ros:humble-ros-base-jammy@sha256:75dd3aba34a3838dadbb31a9f7bef769bdfa8713e6cec686fc868db2981b0987

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iproute2 \
        iputils-ping \
        net-tools \
        python3-colcon-common-extensions \
        python3-pip \
        python3-numpy \
        python3-pytest \
        python3-yaml \
        ros-humble-control-msgs \
        ros-humble-controller-manager \
        ros-humble-forward-command-controller \
        ros-humble-gazebo-ros-pkgs \
        ros-humble-gazebo-ros2-control \
        ros-humble-geometry-msgs \
        ros-humble-joint-state-broadcaster \
        ros-humble-joint-trajectory-controller \
        ros-humble-moveit-common \
        ros-humble-moveit-configs-utils \
        ros-humble-moveit-core \
        ros-humble-moveit-kinematics \
        ros-humble-moveit-msgs \
        ros-humble-moveit-planners-ompl \
        ros-humble-moveit-ros-move-group \
        ros-humble-moveit-ros-planning \
        ros-humble-moveit-ros-planning-interface \
        ros-humble-moveit-servo \
        ros-humble-moveit-simple-controller-manager \
        ros-humble-robot-state-publisher \
        ros-humble-rmw-cyclonedds-cpp \
        ros-humble-rmw-zenoh-cpp \
        ros-humble-ros2-control \
        ros-humble-ros2-controllers \
        ros-humble-rosidl-default-generators \
        ros-humble-rosidl-default-runtime \
        ros-humble-srdfdom \
        ros-humble-std-srvs \
        ros-humble-tf2-geometry-msgs \
        ros-humble-tf2-ros \
        ros-humble-tf2-ros-py \
        ros-humble-xacro \
        tcpdump \
    && rm -rf /var/lib/apt/lists/*

ENV DEBIAN_FRONTEND=
ENV ROS_DOMAIN_ID=42
ENV RMW_IMPLEMENTATION=rmw_zenoh_cpp
ENV CYCLONEDDS_URI=file:///teleop/config/cyclonedds.xml

WORKDIR /teleop/ros2_ws

COPY docker/entrypoint.sh /teleop/entrypoint.sh

ENTRYPOINT ["/teleop/entrypoint.sh"]
CMD ["sleep", "infinity"]
