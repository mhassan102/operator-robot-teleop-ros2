#!/usr/bin/env bash
set -e

source /opt/ros/humble/setup.bash

if [[ -f /teleop/ros2_ws/install/setup.bash ]]; then
  source /teleop/ros2_ws/install/setup.bash
fi

exec "$@"

