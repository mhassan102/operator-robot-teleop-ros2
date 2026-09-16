#!/usr/bin/env bash
# Operator PC: MediaMTX on 127.0.0.1. RTP in from mlink-op send_media :5004.
# WebRTC / WHEP for the console at http://127.0.0.1:8090/ (ICE localhost only).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
mkdir -p logs run

if [[ ! -x "$ROOT/bin/mediamtx" ]]; then
  echo "missing $ROOT/bin/mediamtx (linux_amd64 on the operator PC)." >&2
  echo "Download MediaMTX v1.20.1 linux_amd64 and place the binary at bin/mediamtx. See README.md." >&2
  exit 1
fi

if [[ -f run/mediamtx.pid ]] && kill -0 "$(cat run/mediamtx.pid)" 2>/dev/null; then
  echo "already running (pid $(cat run/mediamtx.pid)). ./stop.sh first." >&2
  exit 1
fi

: > logs/mediamtx.log
nohup ./bin/mediamtx "$ROOT/mediamtx.yml" >> logs/mediamtx.log 2>&1 &
echo $! > run/mediamtx.pid
disown || true

for _ in $(seq 1 50); do
  if ss -lnt | grep -q "127.0.0.1:8889"; then
    break
  fi
  if ss -lnt | grep -q ":8889"; then
    break
  fi
  if ! kill -0 "$(cat run/mediamtx.pid)" 2>/dev/null; then
    echo "mediamtx exited. last log:" >&2
    tail -30 logs/mediamtx.log >&2
    exit 1
  fi
  sleep 0.1
done

if ! ss -lnt | grep -q ":8889"; then
  echo "mediamtx did not bind :8889" >&2
  tail -30 logs/mediamtx.log >&2
  kill "$(cat run/mediamtx.pid)" 2>/dev/null || true
  exit 1
fi

echo "player running (localhost WebRTC)"
echo "  console: http://127.0.0.1:8090/"
echo "  whep:    http://127.0.0.1:8889/cam/whep"
echo "stop with: $ROOT/stop.sh"
