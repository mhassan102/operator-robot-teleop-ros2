#!/usr/bin/env bash
# One-cycle SO-ARM gripper smoke: open one tiny step, hold, close one step.
# Run on the robot host AFTER: ./scripts/start_robot_mlink.sh --real-arm
# Watch http://100.67.47.79:8889/cam/  Do not re-run in a loop.
# Gripper id 6 only; max 48 ticks per step. Then this script exits and
# deadman torque-off (~0.5s). Soft stop: ./scripts/stop_mlink.sh
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"

compose=(docker compose -f compose.robot-mlink.yaml -f compose.robot-mlink.real-arm.yaml)
container="ros2-teleop-robot-mlink"

if ! docker inspect "${container}" >/dev/null 2>&1; then
  echo "ERROR: ${container} is not running. Start with: ./scripts/start_robot_mlink.sh --real-arm" >&2
  exit 1
fi
status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container}")"
if [[ "${status}" != "healthy" && "${status}" != "running" ]]; then
  echo "ERROR: ${container} status=${status}" >&2
  exit 1
fi

echo "gripper test: one open step -> hold 2s -> one close step (id 6, 48 ticks max)"
echo "watch the camera; arm joints must not move. Do not run this twice."

"${compose[@]}" exec -T robot bash -s <<'EOS'
set -eo pipefail
set +u
source /opt/ros/humble/setup.bash
source /teleop/ros2_ws/install/setup.bash
set -u
python3 - <<'PY'
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float64
from teleop_demo_msgs.msg import TeleopState

OPEN_S = 0.4
HOLD_S = 2.0
CLOSE_S = 0.4
RATE_S = 0.01


def burst(pub_s, pub_g, state, gripper, seconds, label):
    print(label, flush=True)
    msg_g = Float64()
    msg_g.data = gripper
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pub_s.publish(state)
        pub_g.publish(msg_g)
        time.sleep(RATE_S)


rclpy.init()
node = Node("test_real_gripper")
pub_g = node.create_publisher(Float64, "/gripper_safe", 10)
pub_s = node.create_publisher(TeleopState, "/teleop/state", 10)
state = TeleopState()
state.connection_state = "CONNECTED"
state.watchdog_state = "OK"
time.sleep(0.2)
burst(pub_s, pub_g, state, 1.0, OPEN_S, "OPEN (tiny step)")
burst(pub_s, pub_g, state, 1.0, HOLD_S, "HOLD")
burst(pub_s, pub_g, state, 0.0, CLOSE_S, "CLOSE (tiny step)")
node.destroy_node()
rclpy.shutdown()
print("done; deadman will torque-off in ~0.5s", flush=True)
PY
EOS

echo "now: ./scripts/stop_mlink.sh"
