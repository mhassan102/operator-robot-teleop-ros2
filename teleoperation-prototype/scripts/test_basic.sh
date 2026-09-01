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

echo "Checking /teleop/command endpoint and message type..."
topic_info="$(docker compose exec -T operator \
  /teleop/entrypoint.sh ros2 topic info /teleop/command --verbose)"
if [[ "${topic_info}" != *"Type: teleop_demo_msgs/msg/TeleopCommand"* ]]; then
  echo "ERROR: /teleop/command is not teleop_demo_msgs/msg/TeleopCommand." >&2
  echo "${topic_info}" >&2
  exit 1
fi

directions=(+x x- +y +yaw stop)
for direction in "${directions[@]}"; do
  echo "Publishing ${direction}..."
  docker compose exec -T operator \
    /teleop/entrypoint.sh ros2 run teleop_demo operator_command \
      --direction "${direction}" --count 3 --rate 10 \
      --ros-args --params-file /teleop/config/teleop.yaml
done

sleep 1
robot_logs="$(docker compose logs --no-color robot)"
for direction in +X -X +Y +YAW STOP; do
  if [[ "${robot_logs}" != *"direction=${direction}"* ]]; then
    echo "ERROR: robot log does not contain ${direction}." >&2
    echo "${robot_logs}" >&2
    exit 1
  fi
done

received_count="$(grep -c 'COMMAND RECEIVED' <<<"${robot_logs}")"
if (( received_count < 15 )); then
  echo "ERROR: expected at least 15 received commands, got ${received_count}." >&2
  exit 1
fi

echo "Publishing 100-message reliable transport burst..."
before_burst="${received_count}"
docker compose exec -T operator \
  /teleop/entrypoint.sh ros2 run teleop_demo operator_command \
    --direction +x --count 100 --rate 100 --quiet \
    --ros-args --params-file /teleop/config/teleop.yaml

burst_deadline=$((SECONDS + 10))
while (( SECONDS < burst_deadline )); do
  received_count="$(docker compose logs --no-color robot | grep -c 'COMMAND RECEIVED')"
  if (( received_count >= before_burst + 100 )); then
    break
  fi
  sleep 1
done
if (( received_count != before_burst + 100 )); then
  echo "ERROR: expected exactly 100 additional messages; received $((received_count - before_burst))." >&2
  exit 1
fi

echo "Checking discovery recovery after robot restart..."
docker compose restart robot >/dev/null
restart_deadline=$((SECONDS + 30))
while (( SECONDS < restart_deadline )); do
  if docker compose exec -T operator \
      /teleop/entrypoint.sh ros2 node list --no-daemon 2>/dev/null \
      | grep -qx '/robot_command_receiver'; then
    break
  fi
  sleep 1
done
if (( SECONDS >= restart_deadline )); then
  echo "ERROR: discovery did not recover after robot restart." >&2
  exit 1
fi

echo "Checking ROS domain isolation..."
isolated_nodes="$(docker compose run --rm --no-deps \
  -e ROS_DOMAIN_ID=43 operator \
  timeout 8 /teleop/entrypoint.sh ros2 node list --no-daemon 2>/dev/null || true)"
if [[ "${isolated_nodes}" == *"/robot_command_receiver"* ]]; then
  echo "ERROR: domain 43 unexpectedly discovered the domain 42 robot." >&2
  exit 1
fi

echo "M2 basic transport PASS: five command types, 100-message burst, restart recovery, and domain isolation verified."
