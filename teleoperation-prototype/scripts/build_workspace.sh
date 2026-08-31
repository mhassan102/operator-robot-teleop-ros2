#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"

docker compose run --rm --no-deps robot \
  colcon build --symlink-install --event-handlers console_direct+
