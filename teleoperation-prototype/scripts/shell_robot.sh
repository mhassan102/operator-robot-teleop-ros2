#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"
exec docker compose exec robot bash -c \
  'source /opt/ros/humble/setup.bash; source /teleop/ros2_ws/install/setup.bash; exec bash'
