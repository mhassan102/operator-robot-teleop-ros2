#!/usr/bin/env bash
# Stop F8 split Compose (operator-mlink and/or robot-mlink). Localhost
# Zenoh compose.yaml is left alone; use ./scripts/stop.sh for that.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"

if [[ -f compose.operator-mlink.yaml ]]; then
  docker compose -f compose.operator-mlink.yaml down --remove-orphans || true
fi
if [[ -f compose.robot-mlink.yaml ]]; then
  docker compose -f compose.robot-mlink.yaml down --remove-orphans || true
fi
