#!/usr/bin/env bash
# Stop F8 split Compose (operator-mlink and/or robot-mlink). Localhost
# Zenoh compose.yaml is left alone; use ./scripts/stop.sh for that.
# F18 Stage 4 soft stop: this PC operator compose, then laptop
# stop_mlink.sh, then Ctrl-C mlink daemons, then video/stop.sh.
# Does not stop mlink-op / mlink-edge (those are host processes).
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"

if [[ -f compose.operator-mlink.yaml ]]; then
  docker compose -f compose.operator-mlink.yaml down --remove-orphans || true
fi
if [[ -f compose.robot-mlink.yaml ]]; then
  robot_compose=(docker compose -f compose.robot-mlink.yaml)
  if [[ -f compose.robot-mlink.real-arm.yaml ]]; then
    robot_compose+=(-f compose.robot-mlink.real-arm.yaml)
  fi
  "${robot_compose[@]}" down --remove-orphans || true
fi
