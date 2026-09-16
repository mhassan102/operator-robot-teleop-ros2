#!/usr/bin/env bash
# Orin nvidia-3 only. Operator stays on the PC. Requires mlink-edge already running.
# Default TELEOP_ARM=gazebo (headless). --real-arm: receiver + mlink, no Gazebo/serial.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"

export TELEOP_GAZEBO_GUI="${TELEOP_GAZEBO_GUI:-false}"

real_arm_flag=0
for arg in "$@"; do
  case "${arg}" in
    --real-arm)
      real_arm_flag=1
      ;;
    *)
      echo "ERROR: unknown argument: ${arg}" >&2
      echo "Usage: $0 [--real-arm]" >&2
      exit 2
      ;;
  esac
done

if (( real_arm_flag )); then
  existing="${TELEOP_ARM:-}"
  if [[ -n "${existing}" && "${existing,,}" != "real" ]]; then
    echo "ERROR: --real-arm conflicts with TELEOP_ARM=${existing}; refusing to fall back to Gazebo" >&2
    exit 1
  fi
  export TELEOP_ARM=real
else
  export TELEOP_ARM="${TELEOP_ARM:-gazebo}"
  export TELEOP_ARM="${TELEOP_ARM,,}"
fi

case "${TELEOP_ARM}" in
  gazebo|real) ;;
  *)
    echo "ERROR: TELEOP_ARM must be gazebo or real (got ${TELEOP_ARM}); refusing to fall back to Gazebo" >&2
    exit 1
    ;;
esac

echo "TELEOP_ARM=${TELEOP_ARM}"
if [[ "${TELEOP_ARM}" == "real" ]]; then
  echo "TELEOP_ARM=real; hardware bridge not started"
fi

if [[ "${TELEOP_ARM_PARSE_ONLY:-}" == "1" ]]; then
  exit 0
fi

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
