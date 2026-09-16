#!/usr/bin/env bash
# Orin nvidia-3 only. Operator stays on the PC. Requires mlink-edge already running.
# Gazebo is headless by default. Build the aarch64 image on this machine first.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"

export TELEOP_GAZEBO_GUI="${TELEOP_GAZEBO_GUI:-false}"
compose=(docker compose -f compose.robot-mlink.yaml)

if [[ ! -f ros2_ws/install/setup.bash ]] || [[ ! -d ros2_ws/install/teleop_demo_msgs ]]; then
  echo "ROS workspace is not built; performing the workspace build."
  "${compose[@]}" run --rm --no-deps robot \
    colcon build --symlink-install --event-handlers console_direct+
fi

"${compose[@]}" up -d robot

deadline=$((SECONDS + 300))
while (( SECONDS < deadline )); do
  container_id="$("${compose[@]}" ps -q robot)"
  if [[ -n "${container_id}" ]]; then
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}")"
    if [[ "${status}" == "healthy" ]]; then
      "${compose[@]}" ps
      exit 0
    fi
  fi
  sleep 2
done

"${compose[@]}" ps
"${compose[@]}" logs --no-color
echo "ERROR: robot did not become healthy within 300 seconds" >&2
exit 1
