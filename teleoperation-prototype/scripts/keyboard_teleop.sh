#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"

if [[ -z "$(docker compose ps -q operator)" ]]; then
  echo "ERROR: operator is not running; execute ./scripts/start.sh first." >&2
  exit 1
fi

exec docker compose exec -it operator \
  /teleop/entrypoint.sh ros2 run teleop_demo keyboard_teleop \
    --ros-args --params-file /teleop/config/teleop.yaml
