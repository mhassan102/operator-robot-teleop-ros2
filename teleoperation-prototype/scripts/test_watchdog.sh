#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"

params=(--ros-args --params-file /teleop/config/teleop.yaml)

linear_x_from_echo() {
  awk '/^linear:/{f=1} f && /x:/{print $2; exit}' <<<"$1"
}

robot_logs() {
  docker compose logs --no-color robot
}

robot_has() {
  [[ "$(robot_logs)" == *"$1"* ]]
}

robot_count() {
  grep -c "$1" <<<"$(robot_logs)" || true
}

for service in operator robot; do
  container_id="$(docker compose ps -q "${service}")"
  if [[ -z "${container_id}" ]]; then
    echo "ERROR: ${service} is not running; execute ./scripts/start.sh first." >&2
    exit 1
  fi
done

echo "Restarting robot for a clean safety log..."
docker compose restart robot >/dev/null

echo "Checking ROS 2 discovery from operator..."
node_deadline=$((SECONDS + 20))
while (( SECONDS < node_deadline )); do
  if docker compose exec -T operator \
      /teleop/entrypoint.sh ros2 node list --no-daemon 2>/dev/null \
      | grep -qx '/robot_command_receiver'; then
    break
  fi
  sleep 1
done
if (( SECONDS >= node_deadline )); then
  echo "ERROR: operator did not discover /robot_command_receiver." >&2
  docker compose logs --no-color robot >&2
  exit 1
fi

robot_logs="$(docker compose logs --no-color robot)"
if [[ "${robot_logs}" != *"SAFETY STATE=SAFE STOP ACTIVATED"* ]]; then
  echo "ERROR: robot did not latch SAFE STOP ACTIVATED at startup." >&2
  echo "${robot_logs}" >&2
  exit 1
fi

echo "Checking safe Cartesian output while jogging..."
docker compose exec -d operator \
  /teleop/entrypoint.sh ros2 run teleop_demo operator_command \
    --direction +x --duration 2 --rate 20 --quiet "${params[@]}"
sleep 0.6
safe_echo="$(docker compose exec -T operator \
  /teleop/entrypoint.sh timeout 2 ros2 topic echo /cmd_vel_safe --once || true)"
if [[ "$(linear_x_from_echo "${safe_echo}")" != 0.05* ]]; then
  echo "ERROR: /cmd_vel_safe did not carry the +x jog:" >&2
  echo "${safe_echo}" >&2
  docker compose logs --no-color robot >&2
  exit 1
fi

echo "Waiting for command stream to end and watchdog to trip..."
sleep 2.5
watchdog_started=$(date +%s%N)
stop_deadline=$((SECONDS + 3))
while (( SECONDS < stop_deadline )); do
  if robot_has 'SAFETY STATE=TIMEOUT'; then
    break
  fi
  sleep 0.05
done
watchdog_elapsed_ms=$(( ($(date +%s%N) - watchdog_started) / 1000000 ))
if ! robot_has 'SAFETY STATE=TIMEOUT'; then
  echo "ERROR: watchdog did not enter TIMEOUT after the operator stopped." >&2
  robot_logs >&2
  exit 1
fi
if ! robot_has 'SAFETY STATE=SAFE STOP ACTIVATED'; then
  echo "ERROR: watchdog did not log SAFE STOP ACTIVATED." >&2
  exit 1
fi
zero_echo="$(docker compose exec -T operator \
  /teleop/entrypoint.sh timeout 2 ros2 topic echo /cmd_vel_safe --once || true)"
if [[ "$(linear_x_from_echo "${zero_echo}")" != 0.0* ]]; then
  echo "ERROR: expected zero twist after watchdog stop:" >&2
  echo "${zero_echo}" >&2
  exit 1
fi
echo "Watchdog tripped; poll window ${watchdog_elapsed_ms} ms after command end."

echo "Starting heartbeat; motion must stay zero until a fresh command..."
docker compose exec -d operator \
  /teleop/entrypoint.sh ros2 run teleop_demo operator_heartbeat "${params[@]}"
sleep 0.4
if ! robot_has 'SAFETY STATE=RESTORED'; then
  echo "ERROR: heartbeat did not restore the connection." >&2
  robot_logs >&2
  exit 1
fi
held_echo="$(docker compose exec -T operator \
  /teleop/entrypoint.sh timeout 2 ros2 topic echo /cmd_vel_safe --once || true)"
if [[ "$(linear_x_from_echo "${held_echo}")" != 0.0* ]]; then
  echo "ERROR: restored connection replayed a stale jog:" >&2
  echo "${held_echo}" >&2
  exit 1
fi

echo "Sending a fresh +x command..."
docker compose exec -d operator \
  /teleop/entrypoint.sh ros2 run teleop_demo operator_command \
    --direction +x --duration 1 --rate 20 --quiet "${params[@]}"
sleep 0.4
fresh_echo="$(docker compose exec -T operator \
  /teleop/entrypoint.sh timeout 2 ros2 topic echo /cmd_vel_safe --once || true)"
if [[ "$(linear_x_from_echo "${fresh_echo}")" != 0.05* ]]; then
  echo "ERROR: fresh command did not reach /cmd_vel_safe:" >&2
  echo "${fresh_echo}" >&2
  docker compose logs --no-color robot >&2
  exit 1
fi
if ! robot_has 'SAFETY STATE=CONNECTED'; then
  echo "ERROR: robot did not log CONNECTED after the fresh command." >&2
  exit 1
fi

echo "Heartbeat continues after commands stop; jog must zero without TIMEOUT..."
sleep 1.2
timeout_count_before="$(robot_count 'SAFETY STATE=TIMEOUT')"
sleep 0.5
idle_echo="$(docker compose exec -T operator \
  /teleop/entrypoint.sh timeout 2 ros2 topic echo /cmd_vel_safe --once || true)"
if [[ "$(linear_x_from_echo "${idle_echo}")" != 0.0* ]]; then
  echo "ERROR: jog was latched after command silence:" >&2
  echo "${idle_echo}" >&2
  exit 1
fi
timeout_count_after="$(robot_count 'SAFETY STATE=TIMEOUT')"
if [[ "${timeout_count_after}" != "${timeout_count_before}" ]]; then
  echo "ERROR: heartbeat-only idle incorrectly tripped the watchdog." >&2
  docker compose logs --no-color robot >&2
  exit 1
fi

docker compose exec -T operator pkill -f operator_heartbeat >/dev/null 2>&1 || true

echo "M4 watchdog PASS: Cartesian jog, timeout safe-stop, no stale replay, heartbeat idle."
