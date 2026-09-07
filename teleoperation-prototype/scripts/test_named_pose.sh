#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"

params=(--ros-args --params-file /teleop/config/teleop.yaml)

pose_xyz() {
  python3 - "$1" <<'PY'
import sys
text = sys.argv[1]
x = y = z = None
in_position = False
for line in text.splitlines():
    stripped = line.strip()
    if stripped.startswith("position:"):
        in_position = True
        continue
    if in_position and stripped.startswith("x:"):
        x = float(stripped.split(":")[1])
    elif in_position and stripped.startswith("y:"):
        y = float(stripped.split(":")[1])
    elif in_position and stripped.startswith("z:"):
        z = float(stripped.split(":")[1])
        break
if x is None or y is None or z is None:
    raise SystemExit("could not parse pose")
print(f"{x} {y} {z}")
PY
}

for service in operator robot; do
  container_id="$(docker compose ps -q "${service}")"
  if [[ -z "${container_id}" ]]; then
    echo "ERROR: ${service} is not running; execute ./scripts/start.sh first." >&2
    exit 1
  fi
done

echo "Waiting for /teleop/go_named_pose..."
svc_deadline=$((SECONDS + 90))
while (( SECONDS < svc_deadline )); do
  if docker compose exec -T operator \
      /teleop/entrypoint.sh ros2 service list --no-daemon 2>/dev/null \
      | grep -qx '/teleop/go_named_pose'; then
    break
  fi
  sleep 2
done
if (( SECONDS >= svc_deadline )); then
  echo "ERROR: /teleop/go_named_pose never appeared." >&2
  docker compose logs --no-color robot >&2
  exit 1
fi

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

read_pose() {
  docker compose exec -T operator \
    /teleop/entrypoint.sh timeout 5 ros2 topic echo /teleop/tool_pose --once || true
}

go_pose() {
  local name="$1"
  local out=""
  echo "Planning to ${name}..."
  out="$(docker compose exec -T operator \
    /teleop/entrypoint.sh ros2 service call /teleop/go_named_pose \
      teleop_demo_msgs/srv/GoNamedPose "{name: ${name}}")"
  if grep -qiE 'success(: |=)true' <<<"${out}"; then
    echo "${name} ok"
    sleep 1
    return 0
  fi
  echo "ERROR: ${name} named pose failed:" >&2
  echo "${out}" >&2
  docker compose logs --no-color --tail=80 robot >&2
  exit 1
}

go_pose home
echo "Recording pose at home..."
before="$(read_pose)"
before_xyz="$(pose_xyz "${before}")"

go_pose fold
folded="$(read_pose)"
folded_xyz="$(pose_xyz "${folded}")"

python3 - ${before_xyz} ${folded_xyz} <<'PY'
import math, sys
bx, by, bz = map(float, sys.argv[1:4])
fx, fy, fz = map(float, sys.argv[4:7])
delta = math.sqrt((fx - bx) ** 2 + (fy - by) ** 2 + (fz - bz) ** 2)
if delta < 0.04:
    raise SystemExit(f"fold barely moved tool pose: {bx, by, bz} -> {fx, fy, fz} (delta={delta})")
print(f"fold moved tool {delta:.4f} m")
PY

go_pose home
after="$(read_pose)"
after_xyz="$(pose_xyz "${after}")"

python3 - ${before_xyz} ${after_xyz} <<'PY'
import math, sys
bx, by, bz = map(float, sys.argv[1:4])
ax, ay, az = map(float, sys.argv[4:7])
delta = math.sqrt((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2)
if delta > 0.05:
    raise SystemExit(f"home did not return near start: {bx, by, bz} -> {ax, ay, az} (delta={delta})")
print(f"home returned within {delta:.4f} m")
PY

echo "Planning remaining named poses..."
for name in ready observe pregrasp retract stow home; do
  go_pose "${name}"
done

echo "Checking Servo still jogs after named pose..."
pre_jog="$(read_pose)"
pre_xyz="$(pose_xyz "${pre_jog}")"
docker compose exec -T operator \
  /teleop/entrypoint.sh ros2 run teleop_demo operator_command \
    --direction +x --duration 2 --rate 20 --quiet "${params[@]}" >/dev/null
post_jog="$(read_pose)"
post_xyz="$(pose_xyz "${post_jog}")"
python3 - ${pre_xyz} ${post_xyz} <<'PY'
import sys
bx, by, bz = map(float, sys.argv[1:4])
ax, ay, az = map(float, sys.argv[4:7])
if abs(ax - bx) < 0.01:
    raise SystemExit(f"tool x barely moved after named pose: {bx} -> {ax}")
print(f"post-pose jog x {bx:.4f} -> {ax:.4f} m")
PY

echo "M7 named pose PASS: fold/home planned and Servo still jogs."
