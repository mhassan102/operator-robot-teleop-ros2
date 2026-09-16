FROM ros:humble-ros-base-jammy@sha256:75dd3aba34a3838dadbb31a9f7bef769bdfa8713e6cec686fc868db2981b0987

ARG TARGETARCH

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive
ENV GAZEBO_ROS_OVERLAY=/opt/gazebo_ros_overlay

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

# amd64: Gazebo Classic ROS wrappers from packages.ros.org (unchanged).
# arm64: those debs are not published; Gazebo 11 comes from the Open Robotics
# non-amd64 PPA and gazebo_ros / gazebo_ros2_control are built from source.
RUN arch="${TARGETARCH:-}"; \
    if [ -z "${arch}" ]; then \
      case "$(uname -m)" in aarch64|arm64) arch=arm64 ;; *) arch=amd64 ;; esac; \
    fi; \
    apt-get update; \
    if [ "${arch}" = "arm64" ]; then \
      apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        gnupg \
        python3-lxml \
        software-properties-common; \
      add-apt-repository -y ppa:openrobotics/gazebo11-non-amd64; \
      apt-get update; \
      apt-get install -y --no-install-recommends \
        gazebo \
        libgazebo-dev \
        ros-humble-gazebo-dev \
        ros-humble-gazebo-msgs; \
    else \
      apt-get install -y --no-install-recommends \
        ros-humble-gazebo-ros-pkgs \
        ros-humble-gazebo-ros2-control; \
    fi; \
    rm -rf /var/lib/apt/lists/*

RUN arch="${TARGETARCH:-}"; \
    if [ -z "${arch}" ]; then \
      case "$(uname -m)" in aarch64|arm64) arch=arm64 ;; *) arch=amd64 ;; esac; \
    fi; \
    if [ "${arch}" != "arm64" ]; then exit 0; fi; \
    mkdir -p /tmp/gz_ws/src; \
    git clone --depth 1 --branch ros2 \
      https://github.com/ros-simulation/gazebo_ros_pkgs.git /tmp/gz_ws/src/gazebo_ros_pkgs && \
    git clone --depth 1 --branch humble \
      https://github.com/ros-controls/gazebo_ros2_control.git /tmp/gz_ws/src/gazebo_ros2_control && \
    touch /tmp/gz_ws/src/gazebo_ros_pkgs/gazebo_dev/COLCON_IGNORE && \
    touch /tmp/gz_ws/src/gazebo_ros_pkgs/gazebo_msgs/COLCON_IGNORE && \
    touch /tmp/gz_ws/src/gazebo_ros_pkgs/gazebo_plugins/COLCON_IGNORE && \
    touch /tmp/gz_ws/src/gazebo_ros_pkgs/gazebo_ros_pkgs/COLCON_IGNORE && \
    touch /tmp/gz_ws/src/gazebo_ros2_control/gazebo_ros2_control_demos/COLCON_IGNORE && \
    cd /tmp/gz_ws && \
    . /opt/ros/humble/setup.bash && \
    colcon build --merge-install --install-base "${GAZEBO_ROS_OVERLAY}" \
      --cmake-args -DBUILD_TESTING=OFF \
      --event-handlers console_direct+ && \
    rm -rf /tmp/gz_ws

ENV DEBIAN_FRONTEND=
ENV ROS_DOMAIN_ID=42
ENV RMW_IMPLEMENTATION=rmw_zenoh_cpp
ENV CYCLONEDDS_URI=file:///teleop/config/cyclonedds.xml

WORKDIR /teleop/ros2_ws

COPY docker/entrypoint.sh /teleop/entrypoint.sh

ENTRYPOINT ["/teleop/entrypoint.sh"]
CMD ["sleep", "infinity"]
