#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_dir}"

docker compose up -d operator robot

deadline=$((SECONDS + 90))
while (( SECONDS < deadline )); do
  unhealthy=0
  for service in operator robot; do
    container_id="$(docker compose ps -q "${service}")"
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}")"
    if [[ "${status}" != "healthy" ]]; then
      unhealthy=1
    fi
  done
  if (( unhealthy == 0 )); then
    docker compose ps
    exit 0
  fi
  sleep 2
done

docker compose ps
docker compose logs --no-color
echo "ERROR: containers did not become healthy within 90 seconds" >&2
exit 1

