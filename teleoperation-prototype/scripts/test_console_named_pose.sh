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

json_field() {
  python3 -c 'import json,sys
raw=open(sys.argv[1], encoding="utf-8").read()
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
elif isinstance(value, bool):
    print("true" if value else "false")
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

post_named_pose() {
  local name="$1"
  local body_file="$2"
  local timeout="${3:-90}"
  curl -sS -o "${body_file}" -w '%{http_code}' --max-time "${timeout}" \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"name": sys.argv[1]}))' "${name}")" \
    http://127.0.0.1:8090/api/named_pose
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

page="$(curl -sf http://127.0.0.1:8090/)"
if ! grep -q 'id="camera"' <<<"${page}"; then
  echo "ERROR: camera video element missing from console page." >&2
  exit 1
fi
if grep -q 'camera: not wired (F6)' <<<"${page}"; then
  echo "ERROR: F6 camera stub still on console page." >&2
  exit 1
fi
if ! grep -q 'data-pose="fold"' <<<"${page}"; then
  echo "ERROR: fold pose button missing from console page." >&2
  exit 1
fi
if grep -qE 'disabled[^>]*>fold</button>' <<<"${page}"; then
  echo "ERROR: fold pose button is still disabled." >&2
  exit 1
fi
if ! grep -q 'bypass the 500 ms jog watchdog until F10' <<<"${page}"; then
  echo "ERROR: watchdog bypass note missing from console page." >&2
  exit 1
fi

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

# rmw_zenoh can drop the first MoveIt action while the graph is still busy.
echo "Waiting for move_group to finish coming up..."
mg_deadline=$((SECONDS + 60))
while (( SECONDS < mg_deadline )); do
  if docker compose logs --no-color --since 2m robot 2>/dev/null | grep -q 'You can start planning now'; then
    break
  fi
  sleep 2
done
sleep 15

echo "Rejecting an unknown pose name..."
bad_body="$(mktemp)"
bad_started=$(date +%s%N)
bad_code="$(post_named_pose 'not_a_pose' "${bad_body}" 5)"
bad_elapsed_ms=$(( ($(date +%s%N) - bad_started) / 1000000 ))
if [[ "${bad_code}" != "400" ]]; then
  echo "ERROR: unknown pose expected HTTP 400, got ${bad_code} body=$(cat "${bad_body}")" >&2
  exit 1
fi
if [[ "$(json_field "${bad_body}" ok)" != "false" ]]; then
  echo "ERROR: unknown pose response ok was not false: $(cat "${bad_body}")" >&2
  exit 1
fi
if (( bad_elapsed_ms > 3000 )); then
  echo "ERROR: unknown pose hung (${bad_elapsed_ms} ms)." >&2
  exit 1
fi
echo "Unknown pose rejected in ${bad_elapsed_ms} ms: $(cat "${bad_body}")"

echo "POST home so fold has somewhere to move from..."
home_body="$(mktemp)"
home_code="$(post_named_pose home "${home_body}" 90)"
if [[ "${home_code}" != "200" ]]; then
  echo "ERROR: POST home expected HTTP 200, got ${home_code} body=$(cat "${home_body}")" >&2
  docker compose logs --no-color --tail=80 robot >&2
  exit 1
fi
if [[ "$(json_field "${home_body}" ok)" != "true" ]]; then
  echo "ERROR: POST home ok was not true: $(cat "${home_body}")" >&2
  docker compose logs --no-color --tail=80 robot >&2
  exit 1
fi
echo "home ok: $(json_field "${home_body}" message)"
sleep 1

before_state="$(api_state)"
before_xyz="$(pose_xyz "${before_state}")"
if [[ -z "${before_xyz}" ]]; then
  echo "ERROR: /api/state pose missing before fold: '${before_state}'." >&2
  exit 1
fi

echo "POST fold in the background; /api/health must still answer..."
fold_body="$(mktemp)"
fold_code_file="$(mktemp)"
(
  set +e
  code="$(post_named_pose fold "${fold_body}" 90)"
  status=$?
  printf '%s\n' "${code:-}" > "${fold_code_file}"
  exit "${status}"
) &
fold_pid=$!

health_ok=0
health_deadline=$((SECONDS + 30))
while kill -0 "${fold_pid}" 2>/dev/null && (( SECONDS < health_deadline )); do
  if curl -sf --max-time 2 http://127.0.0.1:8090/api/health >/dev/null; then
    health_ok=1
    break
  fi
  sleep 0.2
done
if (( health_ok == 0 )); then
  echo "ERROR: GET /api/health failed while named pose was in flight." >&2
  wait "${fold_pid}" || true
  exit 1
fi
echo "GET /api/health still 200 during named pose."

wait "${fold_pid}"
fold_code="$(tr -d '[:space:]' < "${fold_code_file}")"
if [[ "${fold_code}" != "200" ]]; then
  echo "ERROR: POST fold expected HTTP 200, got ${fold_code} body=$(cat "${fold_body}")" >&2
  docker compose logs --no-color --tail=80 robot >&2
  exit 1
fi
if [[ "$(json_field "${fold_body}" ok)" != "true" ]]; then
  echo "ERROR: POST fold ok was not true: $(cat "${fold_body}")" >&2
  docker compose logs --no-color --tail=80 robot >&2
  exit 1
fi
echo "fold ok: $(json_field "${fold_body}" message)"
sleep 1

after_state="$(api_state)"
after_xyz="$(pose_xyz "${after_state}")"
if [[ -z "${after_xyz}" ]]; then
  echo "ERROR: /api/state pose missing after fold: '${after_state}'." >&2
  exit 1
fi
python3 - ${before_xyz} ${after_xyz} <<'PY'
import math, sys
bx, by, bz = map(float, sys.argv[1:4])
fx, fy, fz = map(float, sys.argv[4:7])
delta = math.sqrt((fx - bx) ** 2 + (fy - by) ** 2 + (fz - bz) ** 2)
if delta < 0.04:
    raise SystemExit(f"fold barely moved tool pose: {bx, by, bz} -> {fx, fy, fz} (delta={delta})")
print(f"fold moved tool {delta:.4f} m")
PY

echo "Opening /ws/session; key w must still jog after the named pose..."
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

plusx_deadline=$((SECONDS + 8))
while (( SECONDS < plusx_deadline )); do
  plusx_now="$(robot_count 'direction=+X')"
  if (( plusx_now > plusx_before )); then
    break
  fi
  sleep 0.1
done
if (( $(robot_count 'direction=+X') <= plusx_before )); then
  echo "ERROR: robot logs did not show direction=+X after key w post-pose." >&2
  robot_logs >&2
  exit 1
fi
wait_linear_x 0.05
echo "Post-pose jog +x ok."

echo "Checking HUD/API still updates after named pose..."
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
  echo "ERROR: expected connection_state CONNECTED or RESTORED after pose, got '${live_conn}' from '${live_state}'." >&2
  exit 1
fi
stamp_deadline=$((SECONDS + 8))
live_stamp_later="${live_stamp}"
while (( SECONDS < stamp_deadline )); do
  live_stamp_later="$(pose_stamp "$(api_state)")"
  if [[ -n "${live_stamp_later}" && "${live_stamp_later}" != "${live_stamp}" ]]; then
    break
  fi
  sleep 0.1
done
if [[ -z "${live_stamp_later}" || "${live_stamp_later}" == "${live_stamp}" ]]; then
  echo "ERROR: tool pose not updating after named pose (stamp=${live_stamp})." >&2
  exit 1
fi
echo "HUD/API live after pose: connection=${live_conn} pose ${live_pose} stamp ${live_stamp} -> ${live_stamp_later}"

echo UP w >&"${WS[1]}"
read -r -t 2 _ack <&"${WS[0]}" || true
wait_linear_x 0.0

echo "Dropping the WebSocket; watchdog must reach TIMEOUT..."
timeout_before_close="$(robot_count 'SAFETY STATE=TIMEOUT')"
echo CLOSE >&"${WS[1]}" 2>/dev/null || true
if [[ -n "${ws_pid}" ]]; then
  wait "${ws_pid}" 2>/dev/null || true
  ws_pid=""
fi

stop_deadline=$((SECONDS + 3))
while (( SECONDS < stop_deadline )); do
  if (( $(robot_count 'SAFETY STATE=TIMEOUT') > timeout_before_close )); then
    break
  fi
  sleep 0.05
done
if (( $(robot_count 'SAFETY STATE=TIMEOUT') <= timeout_before_close )); then
  echo "ERROR: watchdog did not enter TIMEOUT after the console socket closed." >&2
  robot_logs >&2
  exit 1
fi
echo "Watchdog TIMEOUT after socket close."

echo "F5.4 named pose PASS: POST fold moved the arm, unknown name 400, health stayed up, w still jogs, HUD live, close trips TIMEOUT."
