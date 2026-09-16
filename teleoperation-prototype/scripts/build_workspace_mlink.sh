#!/usr/bin/env bash
# Colcon build using the F8 split Compose files (same image, mounted ros2_ws).
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"

role="${1:-}"
case "${role}" in
  operator)
    docker compose -f compose.operator-mlink.yaml run --rm --no-deps operator \
      colcon build --symlink-install --event-handlers console_direct+
    ;;
  robot)
    docker compose -f compose.robot-mlink.yaml run --rm --no-deps robot \
      colcon build --symlink-install --event-handlers console_direct+
    ;;
  *)
    echo "Usage: $0 operator|robot" >&2
    exit 2
    ;;
esac
