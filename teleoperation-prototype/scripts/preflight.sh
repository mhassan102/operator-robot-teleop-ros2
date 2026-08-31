#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

required_commands=(docker df tc)
for command_name in "${required_commands[@]}"; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "ERROR: required command not found: ${command_name}" >&2
    exit 1
  fi
done

docker info >/dev/null
docker compose version >/dev/null

available_kib="$(df -Pk "${project_dir}" | awk 'NR == 2 {print $4}')"
minimum_kib=$((25 * 1024 * 1024))
if (( available_kib < minimum_kib )); then
  echo "ERROR: at least 25 GiB free disk is required before building." >&2
  exit 1
fi

echo "Preflight OK"
echo "  Project: ${project_dir}"
echo "  Docker:  $(docker --version)"
echo "  Compose: $(docker compose version --short)"
echo "  Free:    $((available_kib / 1024 / 1024)) GiB"
echo "  Display: ${DISPLAY:-not set}"
echo "  Host qdisc (read-only snapshot):"
tc qdisc show | sed 's/^/    /'

