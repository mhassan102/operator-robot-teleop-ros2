#!/usr/bin/env bash
# Operator PC only. Robot is on Orin. Requires mlink-op already running.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"

export TELEOP_GAZEBO_GUI=false
compose=(docker compose -f compose.operator-mlink.yaml)

if [[ ! -f ros2_ws/install/setup.bash ]] || [[ ! -d ros2_ws/install/teleop_demo_msgs ]]; then
  echo "ROS workspace is not built; performing the workspace build."
  "${compose[@]}" run --rm --no-deps operator \
    colcon build --symlink-install --event-handlers console_direct+
fi

"${compose[@]}" up -d operator

deadline=$((SECONDS + 60))
while (( SECONDS < deadline )); do
  container_id="$("${compose[@]}" ps -q operator)"
  if [[ -n "${container_id}" ]]; then
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}")"
    if [[ "${status}" == "healthy" ]]; then
      "${compose[@]}" ps
      echo "operator console: http://127.0.0.1:8090/"
      exit 0
    fi
  fi
  sleep 2
done

"${compose[@]}" ps
"${compose[@]}" logs --no-color
echo "ERROR: operator did not become healthy within 60 seconds" >&2
exit 1
