#!/usr/bin/env bash
# Robot host (Orin nvidia-3 default, or SO-ARM laptop with --real-arm).
# Operator stays on the PC. Requires mlink-edge already running.
# Default TELEOP_ARM=gazebo (headless, Orin). --real-arm: receiver + Feetech
# gripper (+ mlink), no Gazebo. Real mode mounts TELEOP_SERIAL_PORT
# (default /dev/ttyACM0). Gazebo mode mounts no serial device.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"

export TELEOP_GAZEBO_GUI="${TELEOP_GAZEBO_GUI:-false}"

real_arm_flag=0
serial_port="${TELEOP_SERIAL_PORT:-/dev/ttyACM0}"
args=("$@")
index=0
while (( index < ${#args[@]} )); do
  arg="${args[$index]}"
  case "${arg}" in
    --real-arm)
      real_arm_flag=1
      ;;
    --serial-port)
      index=$((index + 1))
      if (( index >= ${#args[@]} )); then
        echo "ERROR: --serial-port requires a path" >&2
        echo "Usage: $0 [--real-arm] [--serial-port PATH]" >&2
        exit 2
      fi
      serial_port="${args[$index]}"
      if [[ -z "${serial_port}" || "${serial_port}" == -* ]]; then
        echo "ERROR: --serial-port requires a path" >&2
        echo "Usage: $0 [--real-arm] [--serial-port PATH]" >&2
        exit 2
      fi
      ;;
    --serial-port=*)
      serial_port="${arg#--serial-port=}"
      if [[ -z "${serial_port}" || "${serial_port}" == -* ]]; then
        echo "ERROR: --serial-port requires a path" >&2
        echo "Usage: $0 [--real-arm] [--serial-port PATH]" >&2
        exit 2
      fi
      ;;
    *)
      echo "ERROR: unknown argument: ${arg}" >&2
      echo "Usage: $0 [--real-arm] [--serial-port PATH]" >&2
      exit 2
      ;;
  esac
  index=$((index + 1))
done
export TELEOP_SERIAL_PORT="${serial_port}"

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
  echo "TELEOP_ARM=real; feetech gripper on ${TELEOP_SERIAL_PORT}"
fi

if [[ "${TELEOP_ARM_PARSE_ONLY:-}" == "1" ]]; then
  exit 0
fi

compose=(docker compose -f compose.robot-mlink.yaml)
if [[ "${TELEOP_ARM}" == "real" ]]; then
  compose+=(-f compose.robot-mlink.real-arm.yaml)
  if [[ ! -e "${TELEOP_SERIAL_PORT}" ]]; then
    echo "ERROR: ${TELEOP_SERIAL_PORT} missing; refusing to fall back to Gazebo" >&2
    exit 1
  fi
  if python3 -c "
import glob, os, sys
port = os.environ.get('TELEOP_SERIAL_PORT', '/dev/ttyACM0')
try:
    target = os.stat(port)
except OSError:
    sys.exit(0)

def docker_pid(pid):
    try:
        text = open('/proc/%d/cgroup' % pid, encoding='utf-8').read()
    except OSError:
        return False
    return 'docker' in text or 'containerd' in text

for fd in glob.glob('/proc/[0-9]*/fd/*'):
    try:
        pid = int(fd.split('/')[2])
        opened = os.stat(fd)
    except (OSError, ValueError, IndexError):
        continue
    if opened.st_dev == target.st_dev and opened.st_ino == target.st_ino:
        if not docker_pid(pid):
            sys.exit(1)
sys.exit(0)
"; then
    :
  else
    echo "ERROR: ${TELEOP_SERIAL_PORT} is busy (another process owns it, e.g. LeRobot); refusing to fall back to Gazebo" >&2
    exit 1
  fi
fi

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
