#!/usr/bin/env bash
set -e

source /opt/ros/humble/setup.bash

zenoh_router_listening() {
  (echo >/dev/tcp/127.0.0.1/7447) >/dev/null 2>&1
}

if [[ "${RMW_IMPLEMENTATION:-}" == "rmw_zenoh_cpp" && "${TELEOP_RUN_ZENOH_ROUTER:-0}" == "1" ]]; then
  if ! zenoh_router_listening; then
    ros2 run rmw_zenoh_cpp rmw_zenohd &
    for _ in $(seq 1 50); do
      if zenoh_router_listening; then
        break
      fi
      sleep 0.1
    done
    if ! zenoh_router_listening; then
      echo "ERROR: rmw_zenohd did not listen on TCP 7447" >&2
      exit 1
    fi
  fi
fi

if [[ -f /teleop/ros2_ws/install/setup.bash ]]; then
  source /teleop/ros2_ws/install/setup.bash
fi

exec "$@"

