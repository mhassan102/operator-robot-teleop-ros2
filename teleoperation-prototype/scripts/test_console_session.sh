#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"

linear_x_from_echo() {
  awk '/^linear:/{f=1} f && /x:/{print $2; exit}' <<<"$1"
}

robot_logs() {
  docker compose logs --no-color robot
}

robot_count() {
  grep -c "$1" <<<"$(robot_logs)" || true
}

echo_safe() {
  docker compose exec -T operator \
    /teleop/entrypoint.sh timeout 2 ros2 topic echo /cmd_vel_safe --once || true
}

wait_linear_x() {
  local want="$1"
  local deadline=$((SECONDS + 8))
  local safe_echo=""
  local got=""
  while (( SECONDS < deadline )); do
    safe_echo="$(echo_safe)"
    got="$(linear_x_from_echo "${safe_echo}")"
    if [[ "${got}" == ${want}* ]]; then
      return 0
    fi
    sleep 0.1
  done
  echo "ERROR: expected /cmd_vel_safe linear.x ${want}*, last was '${got}'" >&2
  echo "${safe_echo}" >&2
  docker compose logs --no-color robot >&2
  return 1
}

for service in operator robot; do
  container_id="$(docker compose ps -q "${service}")"
  if [[ -z "${container_id}" ]]; then
    echo "ERROR: ${service} is not running; execute ./scripts/start.sh first." >&2
    exit 1
  fi
done

if ! curl -sf http://127.0.0.1:8090/api/health >/dev/null; then
  echo "ERROR: GET /api/health failed." >&2
  exit 1
fi

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

robot_logs_now="$(robot_logs)"
if [[ "${robot_logs_now}" != *"SAFETY STATE=SAFE STOP ACTIVATED"* ]]; then
  echo "ERROR: robot did not latch SAFE STOP ACTIVATED at startup." >&2
  echo "${robot_logs_now}" >&2
  exit 1
fi

echo "Opening /ws/session from the host..."
coproc WS { python3 -u "${project_dir}/scripts/console_ws.py"; }
ws_pid="${WS_PID}"
cleanup() {
  if [[ -n "${ws_pid:-}" ]] && kill -0 "${ws_pid}" 2>/dev/null; then
    echo CLOSE >&"${WS[1]}" 2>/dev/null || true
    wait "${ws_pid}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

session_deadline=$((SECONDS + 5))
session_line=""
while (( SECONDS < session_deadline )); do
  if IFS= read -r -t 1 session_line <&"${WS[0]}"; then
    break
  fi
done
if [[ "${session_line}" != SESSION* ]]; then
  echo "ERROR: WebSocket session did not start: '${session_line}'" >&2
  exit 1
fi
echo "Console ${session_line}"

plusx_before="$(robot_count 'direction=+X')"
echo DOWN w >&"${WS[1]}"
read -r -t 2 _ack <&"${WS[0]}" || true

echo "Waiting for +x jog from key w..."
plusx_deadline=$((SECONDS + 8))
while (( SECONDS < plusx_deadline )); do
  plusx_now="$(robot_count 'direction=+X')"
  if (( plusx_now > plusx_before )); then
    break
  fi
  sleep 0.1
done
if ! grep -q 'COMMAND RECEIVED' <<<"$(robot_logs)"; then
  echo "ERROR: robot logs have no COMMAND RECEIVED after key w." >&2
  robot_logs >&2
  exit 1
fi
if (( $(robot_count 'direction=+X') <= plusx_before )); then
  echo "ERROR: robot logs did not show direction=+X after key w." >&2
  robot_logs >&2
  exit 1
fi
wait_linear_x 0.05

echo "Releasing w; jog must zero while the socket stays open..."
echo UP w >&"${WS[1]}"
read -r -t 2 _ack <&"${WS[0]}" || true
wait_linear_x 0.0

echo "Stop while socket open (blur/hidden equivalent); heartbeat must hold watchdog..."
timeout_before="$(robot_count 'SAFETY STATE=TIMEOUT')"
echo STOP >&"${WS[1]}"
read -r -t 2 _ack <&"${WS[0]}" || true
sleep 1.2
wait_linear_x 0.0
timeout_idle="$(robot_count 'SAFETY STATE=TIMEOUT')"
if [[ "${timeout_idle}" != "${timeout_before}" ]]; then
  echo "ERROR: stop-with-socket-open tripped TIMEOUT (heartbeat should continue)." >&2
  robot_logs >&2
  exit 1
fi

echo "Dropping the WebSocket; watchdog must reach TIMEOUT..."
timeout_before_close="$(robot_count 'SAFETY STATE=TIMEOUT')"
echo CLOSE >&"${WS[1]}" 2>/dev/null || true
if [[ -n "${ws_pid}" ]]; then
  wait "${ws_pid}" 2>/dev/null || true
  ws_pid=""
fi

watchdog_started=$(date +%s%N)
stop_deadline=$((SECONDS + 3))
while (( SECONDS < stop_deadline )); do
  if (( $(robot_count 'SAFETY STATE=TIMEOUT') > timeout_before_close )); then
    break
  fi
  sleep 0.05
done
watchdog_elapsed_ms=$(( ($(date +%s%N) - watchdog_started) / 1000000 ))
if (( $(robot_count 'SAFETY STATE=TIMEOUT') <= timeout_before_close )); then
  echo "ERROR: watchdog did not enter TIMEOUT after the console socket closed." >&2
  robot_logs >&2
  exit 1
fi
echo "Watchdog TIMEOUT after socket close; poll window ${watchdog_elapsed_ms} ms."

echo "F5.2 console session PASS: +x jog, release zeros, blur-stop keeps heartbeat, close trips TIMEOUT."
