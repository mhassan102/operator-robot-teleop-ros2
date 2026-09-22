#!/usr/bin/env bash
# Operator PC only. Requires mlink-op already running.
# Default: Orin F8 console URL. --remote-laptop: SO-ARM Tailscale camera
# URL + TELEOP_GRIPPER_ONLY=1 (g/h only). Does not pick mlink YAML;
# that is mlink-transport/scripts/start_daemon.sh --remote-laptop.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"

export TELEOP_GAZEBO_GUI=false

remote_laptop=0
for arg in "$@"; do
  case "${arg}" in
    --remote-laptop)
      remote_laptop=1
      ;;
    *)
      echo "ERROR: unknown argument: ${arg}" >&2
      echo "Usage: $0 [--remote-laptop]" >&2
      exit 2
      ;;
  esac
done

REMOTE_LAPTOP_CAM="${REMOTE_LAPTOP_CAM:-http://100.120.193.52:8889/cam}"
if (( remote_laptop )); then
  export TELEOP_GRIPPER_ONLY="${TELEOP_GRIPPER_ONLY:-1}"
  console_url="http://127.0.0.1:8090/?cam=${REMOTE_LAPTOP_CAM}"
else
  console_url="http://127.0.0.1:8090/"
fi

echo "TELEOP_GRIPPER_ONLY=${TELEOP_GRIPPER_ONLY:-0}"
echo "operator console: ${console_url}"

if [[ "${TELEOP_OPERATOR_PARSE_ONLY:-}" == "1" ]]; then
  exit 0
fi

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
      echo "operator console: ${console_url}"
      exit 0
    fi
  fi
  sleep 2
done

"${compose[@]}" ps
"${compose[@]}" logs --no-color
echo "ERROR: operator did not become healthy within 60 seconds" >&2
exit 1
