#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"

for service in operator robot; do
  container_id="$(docker compose ps -q "${service}")"
  if [[ -z "${container_id}" ]]; then
    echo "ERROR: ${service} is not running; execute ./scripts/start.sh first." >&2
    exit 1
  fi
done

params=(--ros-args --params-file /teleop/config/teleop.yaml)

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

field() {
  local line="$1"
  local key="$2"
  if [[ "${line}" =~ (^|[[:space:]])${key}=([^[:space:]]+) ]]; then
    printf '%s\n' "${BASH_REMATCH[2]}"
  fi
}

run_operator() {
  docker compose exec -T operator \
    /teleop/entrypoint.sh ros2 run teleop_demo operator_command "$@" "${params[@]}" 2>&1
}

echo "Checking /teleop/command and /teleop/ack types..."
command_info="$(docker compose exec -T operator \
  /teleop/entrypoint.sh ros2 topic info /teleop/command --verbose)"
if [[ "${command_info}" != *"Type: teleop_demo_msgs/msg/TeleopCommand"* ]]; then
  echo "ERROR: /teleop/command is not teleop_demo_msgs/msg/TeleopCommand." >&2
  echo "${command_info}" >&2
  exit 1
fi
ack_info="$(docker compose exec -T operator \
  /teleop/entrypoint.sh ros2 topic info /teleop/ack --verbose)"
if [[ "${ack_info}" != *"Type: teleop_demo_msgs/msg/TeleopAck"* ]]; then
  echo "ERROR: /teleop/ack is not teleop_demo_msgs/msg/TeleopAck." >&2
  echo "${ack_info}" >&2
  exit 1
fi

echo "Sending a 20 Hz sequence for 30 seconds..."
baseline_started=$SECONDS
baseline_output="$(run_operator --direction forward --duration 30 --quiet)"
baseline_elapsed=$((SECONDS - baseline_started))
echo "${baseline_output}"
if (( baseline_elapsed < 29 )); then
  echo "ERROR: 20 Hz/30 s run finished in ${baseline_elapsed}s; publisher is not rate-limited." >&2
  exit 1
fi
baseline_stats="$(grep 'OPERATOR STATS' <<<"${baseline_output}" | tail -1 || true)"
if [[ -z "${baseline_stats}" ]]; then
  echo "ERROR: operator did not print OPERATOR STATS." >&2
  exit 1
fi

session="$(field "${baseline_stats}" session)"
sent="$(field "${baseline_stats}" sent)"
acks="$(field "${baseline_stats}" acks)"
missing_acks="$(field "${baseline_stats}" missing_acks)"
one_way_avg="$(field "${baseline_stats}" one_way_avg_ns)"
one_way_min="$(field "${baseline_stats}" one_way_min_ns)"
one_way_max="$(field "${baseline_stats}" one_way_max_ns)"
rtt_avg="$(field "${baseline_stats}" rtt_avg_ns)"

if [[ "${sent}" != "600" ]]; then
  echo "ERROR: expected 600 sent commands at 20 Hz for 30 s, got ${sent}." >&2
  exit 1
fi
if [[ "${acks}" != "600" || "${missing_acks}" != "0" ]]; then
  echo "ERROR: baseline acknowledgement loss: ${baseline_stats}" >&2
  docker compose logs --no-color robot >&2
  exit 1
fi
if [[ -z "${one_way_avg}" || "${one_way_avg}" -lt 0 ]]; then
  echo "ERROR: one-way latency was not recorded." >&2
  exit 1
fi
if [[ "${one_way_max}" -ge 100000000 ]]; then
  echo "ERROR: one-way max latency ${one_way_max} ns exceeds 100 ms on the local network." >&2
  exit 1
fi
if [[ "${one_way_min}" -lt 0 || "${rtt_avg}" -lt 0 ]]; then
  echo "ERROR: timestamps are not using a consistent clock: ${baseline_stats}" >&2
  exit 1
fi
if [[ "${rtt_avg}" -lt "${one_way_avg}" ]]; then
  echo "ERROR: RTT ${rtt_avg} ns is smaller than one-way ${one_way_avg} ns." >&2
  exit 1
fi

robot_stats="$(docker compose logs --no-color robot | grep "STATS session=${session} " | tail -1 || true)"
if [[ -z "${robot_stats}" ]]; then
  echo "ERROR: robot did not log STATS for session ${session}." >&2
  docker compose logs --no-color robot >&2
  exit 1
fi
if [[ "$(field "${robot_stats}" missing)" != "0" ]]; then
  echo "ERROR: baseline missing commands: ${robot_stats}" >&2
  exit 1
fi
if [[ "$(field "${robot_stats}" duplicates)" != "0" ]]; then
  echo "ERROR: baseline duplicate commands: ${robot_stats}" >&2
  exit 1
fi
if [[ "$(field "${robot_stats}" out_of_order)" != "0" ]]; then
  echo "ERROR: baseline out-of-order commands: ${robot_stats}" >&2
  exit 1
fi
if [[ "$(field "${robot_stats}" unique)" != "600" ]]; then
  echo "ERROR: expected 600 unique commands, got ${robot_stats}" >&2
  exit 1
fi

echo "Repeating a 100-command unimpaired burst..."
repeat_output="$(run_operator --direction forward --count 100 --rate 20 --quiet)"
repeat_stats="$(grep 'OPERATOR STATS' <<<"${repeat_output}" | tail -1 || true)"
if [[ "$(field "${repeat_stats}" sent)" != "100" || "$(field "${repeat_stats}" missing_acks)" != "0" ]]; then
  echo "ERROR: repeated baseline run had loss: ${repeat_stats}" >&2
  exit 1
fi

echo "Injecting a duplicate sequence..."
dup_output="$(run_operator --direction forward --count 5 --rate 20 --inject duplicate --quiet)"
dup_stats="$(grep 'OPERATOR STATS' <<<"${dup_output}" | tail -1 || true)"
dup_session="$(field "${dup_stats}" session)"
dup_robot="$(docker compose logs --no-color robot | grep "STATS session=${dup_session} " | tail -1 || true)"
if [[ "$(field "${dup_robot}" duplicates)" -lt 1 ]]; then
  echo "ERROR: duplicate injection did not increment duplicates: ${dup_robot}" >&2
  exit 1
fi

echo "Injecting a reordered sequence..."
reorder_output="$(run_operator --direction forward --count 5 --rate 20 --inject reorder --quiet)"
reorder_stats="$(grep 'OPERATOR STATS' <<<"${reorder_output}" | tail -1 || true)"
reorder_session="$(field "${reorder_stats}" session)"
reorder_robot="$(docker compose logs --no-color robot | grep "STATS session=${reorder_session} " | tail -1 || true)"
if [[ "$(field "${reorder_robot}" out_of_order)" -lt 1 ]]; then
  echo "ERROR: reorder injection did not increment out_of_order: ${reorder_robot}" >&2
  exit 1
fi

echo "Checking statistics reset on a new session..."
reset_output="$(run_operator --direction stop --count 5 --rate 20 --quiet)"
reset_stats="$(grep 'OPERATOR STATS' <<<"${reset_output}" | tail -1 || true)"
reset_session="$(field "${reset_stats}" session)"
if [[ "${reset_session}" == "${session}" ]]; then
  echo "ERROR: new operator run reused session ${session}." >&2
  exit 1
fi
robot_logs="$(docker compose logs --no-color robot)"
if [[ "${robot_logs}" != *"SESSION RESET previous=${reorder_session} new=${reset_session}"* ]]; then
  echo "ERROR: robot did not log SESSION RESET from ${reorder_session} to ${reset_session}." >&2
  echo "${robot_logs}" >&2
  exit 1
fi
reset_robot="$(grep "STATS session=${reset_session} " <<<"${robot_logs}" | tail -1 || true)"
if [[ "$(field "${reset_robot}" received)" != "5" || "$(field "${reset_robot}" unique)" != "5" ]]; then
  echo "ERROR: counters did not reset for the new session: ${reset_robot}" >&2
  exit 1
fi

echo "M3 delivery PASS: 20 Hz/30 s baseline, repeated zero-loss burst, duplicate/reorder counters, and session reset verified."
