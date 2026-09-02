#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"

params=(--ros-args --params-file /teleop/config/teleop.yaml)

for service in operator robot; do
  container_id="$(docker compose ps -q "${service}")"
  if [[ -z "${container_id}" ]]; then
    echo "ERROR: ${service} is not running; execute ./scripts/start.sh first." >&2
    exit 1
  fi
done

echo "Waiting for /teleop/tool_pose..."
pose_deadline=$((SECONDS + 60))
while (( SECONDS < pose_deadline )); do
  if docker compose exec -T operator \
      /teleop/entrypoint.sh ros2 topic list --no-daemon 2>/dev/null \
      | grep -qx '/teleop/tool_pose'; then
    break
  fi
  sleep 2
done
if (( SECONDS >= pose_deadline )); then
  echo "ERROR: /teleop/tool_pose never appeared." >&2
  docker compose logs --no-color robot >&2
  exit 1
fi

pose_x() {
  awk '/position:/{f=1} f && /x:/{print $2; exit}' <<<"$1"
}

echo "Recording initial tool pose..."
before="$(docker compose exec -T operator \
  /teleop/entrypoint.sh timeout 5 ros2 topic echo /teleop/tool_pose --once || true)"
before_x="$(pose_x "${before}")"
if [[ -z "${before_x}" ]]; then
  echo "ERROR: could not read initial tool pose:" >&2
  echo "${before}" >&2
  exit 1
fi

echo "Jogging +x for 2 seconds..."
docker compose exec -T operator \
  /teleop/entrypoint.sh ros2 run teleop_demo operator_command \
    --direction +x --duration 2 --rate 20 --quiet "${params[@]}" >/dev/null

after="$(docker compose exec -T operator \
  /teleop/entrypoint.sh timeout 5 ros2 topic echo /teleop/tool_pose --once || true)"
after_x="$(pose_x "${after}")"
if [[ -z "${after_x}" ]]; then
  echo "ERROR: could not read tool pose after jog:" >&2
  echo "${after}" >&2
  exit 1
fi

python3 - "${before_x}" "${after_x}" <<'PY'
import sys
before = float(sys.argv[1])
after = float(sys.argv[2])
delta = after - before
if abs(delta) < 0.01:
    raise SystemExit(f"tool x barely moved: {before} -> {after} (delta={delta})")
print(f"tool x moved {before:.4f} -> {after:.4f} m (delta={delta:.4f})")
PY

echo "Waiting for watchdog to freeze the arm..."
sleep 0.8
frozen="$(docker compose exec -T operator \
  /teleop/entrypoint.sh timeout 5 ros2 topic echo /teleop/tool_pose --once || true)"
later="$(docker compose exec -T operator \
  /teleop/entrypoint.sh timeout 5 ros2 topic echo /teleop/tool_pose --once || true)"
python3 - "$(pose_x "${frozen}")" "$(pose_x "${later}")" <<'PY'
import sys
first = float(sys.argv[1])
second = float(sys.argv[2])
if abs(second - first) > 0.005:
    raise SystemExit(f"arm kept moving after watchdog: {first} -> {second}")
print("tool pose held after watchdog")
PY

echo "M5 sim PASS: Gazebo arm moved on +x and stopped after watchdog."
