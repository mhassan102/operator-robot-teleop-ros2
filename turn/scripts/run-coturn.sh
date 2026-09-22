#!/bin/bash
# Start coturn (Docker, host network, UDP only). Does not write a TURN server.
# Reads scripts/coturn.env. Idempotent: replaces a container named coturn.
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -f coturn.env ]]; then
  echo "missing $(pwd)/coturn.env — copy coturn.env.example and set TURN_PASSWORD" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
. ./coturn.env
set +a
: "${TURN_PUBLIC_IP:?}"
: "${TURN_PRIVATE_IP:?}"
: "${TURN_USER:?}"
: "${TURN_PASSWORD:?}"
TURN_REALM="${TURN_REALM:-teleop.lab}"
TURN_MIN_PORT="${TURN_MIN_PORT:-50000}"
TURN_MAX_PORT="${TURN_MAX_PORT:-50100}"
IMAGE="${COTURN_IMAGE:-coturn/coturn:4.6.2}"

if docker info >/dev/null 2>&1; then
  docker_cmd() { docker "$@"; }
else
  docker_cmd() { sudo docker "$@"; }
fi

docker_cmd rm -f coturn >/dev/null 2>&1 || true
docker_cmd run -d --name coturn --network host --restart unless-stopped \
  "$IMAGE" \
  -n \
  --log-file=stdout \
  --listening-port=3478 \
  --fingerprint \
  --lt-cred-mech \
  --realm="$TURN_REALM" \
  --user="${TURN_USER}:${TURN_PASSWORD}" \
  --external-ip="${TURN_PUBLIC_IP}/${TURN_PRIVATE_IP}" \
  --relay-ip="$TURN_PRIVATE_IP" \
  --min-port="$TURN_MIN_PORT" \
  --max-port="$TURN_MAX_PORT" \
  --no-multicast-peers \
  --no-cli \
  --no-tcp \
  --no-tls \
  --no-dtls

echo "coturn container started (UDP ${TURN_PUBLIC_IP}:3478, relay ${TURN_MIN_PORT}-${TURN_MAX_PORT})"
