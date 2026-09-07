#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"

name="${1:-}"
allowed="home|fold|ready|observe|pregrasp|retract|stow"
if [[ ! "${name}" =~ ^(home|fold|ready|observe|pregrasp|retract|stow)$ ]]; then
  echo "usage: $0 ${allowed}" >&2
  exit 1
fi

if [[ -z "$(docker compose ps -q operator)" ]]; then
  echo "ERROR: operator is not running; execute ./scripts/start.sh first." >&2
  exit 1
fi

exec docker compose exec -T operator \
  /teleop/entrypoint.sh ros2 service call /teleop/go_named_pose \
    teleop_demo_msgs/srv/GoNamedPose "{name: ${name}}"
