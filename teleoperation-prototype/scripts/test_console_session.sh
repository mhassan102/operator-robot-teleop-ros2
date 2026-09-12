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

api_state() {
  curl -sf http://127.0.0.1:8090/api/state || true
}

pose_xyz() {
  python3 -c 'import json,sys
try:
    pose=json.loads(sys.argv[1]).get("pose") or {}
except Exception:
    print("")
    raise SystemExit
if not all(k in pose for k in ("x","y","z")):
    print("")
else:
    print("%.6f %.6f %.6f" % (float(pose["x"]), float(pose["y"]), float(pose["z"])))
' "$1"
}

pose_stamp() {
  python3 -c 'import json,sys
try:
    pose=json.loads(sys.argv[1]).get("pose") or {}
except Exception:
    print("")
    raise SystemExit
if "stamp_sec" not in pose or "stamp_nanosec" not in pose:
    print("")
else:
    print("%s.%09d" % (int(pose["stamp_sec"]), int(pose["stamp_nanosec"])))
' "$1"
}

state_field() {
  python3 -c 'import json,sys
raw=sys.argv[1]
key=sys.argv[2]
try:
    data=json.loads(raw)
except Exception:
    print("")
    raise SystemExit
value=data
for part in key.split("."):
    if not isinstance(value, dict):
        value=None
        break
    value=value.get(part)
if value is None:
    print("")
else:
    print(value)
' "$1" "$2"
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

echo "Waiting for a fresh /teleop/tool_pose snapshot..."
stale_state="$(api_state)"
stale_stamp="$(pose_stamp "${stale_state}")"
pose_deadline=$((SECONDS + 30))
fresh_state=""
fresh_stamp=""
while (( SECONDS < pose_deadline )); do
  fresh_state="$(api_state)"
  fresh_stamp="$(pose_stamp "${fresh_state}")"
  if [[ -n "${fresh_stamp}" && "${fresh_stamp}" != "${stale_stamp}" ]]; then
    break
  fi
  sleep 0.2
done
if [[ -z "${fresh_stamp}" || "${fresh_stamp}" == "${stale_stamp}" ]]; then
  echo "ERROR: /api/state pose did not update after robot restart (stamp='${fresh_stamp}')." >&2
  echo "${fresh_state}" >&2
  docker compose logs --no-color robot >&2
  exit 1
fi
echo "Tool pose live at stamp ${fresh_stamp}"

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

echo "Checking live telemetry while jogging..."
live_deadline=$((SECONDS + 8))
live_state=""
live_conn=""
live_pose=""
live_stamp=""
while (( SECONDS < live_deadline )); do
  live_state="$(api_state)"
  live_conn="$(state_field "${live_state}" connection_state)"
  live_pose="$(pose_xyz "${live_state}")"
  live_stamp="$(pose_stamp "${live_state}")"
  if [[ "${live_conn}" == "CONNECTED" || "${live_conn}" == "RESTORED" ]] \
      && [[ -n "${live_pose}" ]] && [[ -n "${live_stamp}" ]]; then
    break
  fi
  sleep 0.1
done
if [[ "${live_conn}" != "CONNECTED" && "${live_conn}" != "RESTORED" ]]; then
  echo "ERROR: expected connection_state CONNECTED or RESTORED while jogging, got '${live_conn}' from '${live_state}'." >&2
  exit 1
fi
if [[ -z "${live_pose}" ]]; then
  echo "ERROR: tool pose missing from /api/state while jogging: '${live_state}'." >&2
  exit 1
fi
stamp_deadline=$((SECONDS + 8))
live_state_later="${live_state}"
live_stamp_later="${live_stamp}"
while (( SECONDS < stamp_deadline )); do
  live_state_later="$(api_state)"
  live_stamp_later="$(pose_stamp "${live_state_later}")"
  if [[ -n "${live_stamp_later}" && "${live_stamp_later}" != "${live_stamp}" ]]; then
    break
  fi
  sleep 0.1
done
if [[ -z "${live_stamp_later}" || "${live_stamp_later}" == "${live_stamp}" ]]; then
  echo "ERROR: tool pose not updating (stuck at ${live_pose} stamp=${live_stamp})." >&2
  echo "${live_state}" >&2
  echo "${live_state_later}" >&2
  exit 1
fi
echo "HUD/API live: connection=${live_conn} pose ${live_pose} stamp ${live_stamp} -> ${live_stamp_later}"

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

echo "Checking display-only /api/state after socket close..."
closed_deadline=$((SECONDS + 3))
closed_state=""
closed_conn=""
closed_watchdog=""
while (( SECONDS < closed_deadline )); do
  closed_state="$(api_state)"
  closed_conn="$(state_field "${closed_state}" connection_state)"
  closed_watchdog="$(state_field "${closed_state}" watchdog_state)"
  if [[ "${closed_conn}" == "TIMEOUT" ]]; then
    break
  fi
  sleep 0.05
done
if [[ "${closed_conn}" != "TIMEOUT" ]]; then
  echo "ERROR: GET /api/state did not show TIMEOUT after socket close: '${closed_state}'." >&2
  robot_logs >&2
  exit 1
fi
if [[ "${closed_watchdog}" != "SAFE STOP ACTIVATED" ]]; then
  echo "ERROR: GET /api/state watchdog_state was '${closed_watchdog}', expected SAFE STOP ACTIVATED." >&2
  echo "${closed_state}" >&2
  robot_logs >&2
  exit 1
fi
echo "GET /api/state after close: connection=${closed_conn} watchdog=${closed_watchdog}"

page="$(curl -sf http://127.0.0.1:8090/)"
if ! grep -q 'camera: not wired (F6)' <<<"${page}"; then
  echo "ERROR: camera placeholder missing from console page." >&2
  exit 1
fi
if ! grep -q 'disabled>home</button>' <<<"${page}"; then
  echo "ERROR: named-pose buttons are not disabled." >&2
  exit 1
fi

echo "F5.3 console session PASS: +x jog, live CONNECTED+pose, release zeros, blur-stop keeps heartbeat, close trips TIMEOUT on robot and /api/state."
