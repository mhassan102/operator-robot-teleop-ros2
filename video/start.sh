#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
mkdir -p logs run

RESOLUTION="${RESOLUTION:-}"
TESTSRC="${TESTSRC:-0}"
for arg in "$@"; do
  case "$arg" in
    720p|--720p) RESOLUTION=720p ;;
    --testsrc) TESTSRC=1 ;;
    -h|--help)
      echo "Usage: $0 [--720p] [--testsrc]"
      echo "  default: /dev/video0 640x480 MJPG -> NVENC H.264 -> WebRTC"
      echo "  --720p    1280x720"
      echo "  --testsrc SMPTE bars (does not use the USB camera)"
      echo
      echo "Open in Chrome:"
      echo "  http://127.0.0.1:8090/"
      echo "  http://100.101.94.5:8889/cam"
      echo "  http://nvidia-3.tail40aa1c.ts.net:8889/cam"
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done
export RESOLUTION TESTSRC

if [[ ! -x "$ROOT/bin/mediamtx" ]]; then
  echo "missing $ROOT/bin/mediamtx (linux_arm64)." >&2
  echo "Download MediaMTX v1.20.1 and place the binary at bin/mediamtx. See README.md." >&2
  exit 1
fi

if [[ -f run/mediamtx.pid ]] && kill -0 "$(cat run/mediamtx.pid)" 2>/dev/null; then
  echo "already running (pid $(cat run/mediamtx.pid)). ./stop.sh first." >&2
  exit 1
fi

if [[ "$TESTSRC" != "1" ]]; then
  if [[ ! -e /dev/video0 ]]; then
    echo "missing /dev/video0" >&2
    exit 1
  fi
  holder=""
  if sudo -n fuser /dev/video0 >/dev/null 2>&1; then
    holder="$(sudo -n fuser -v /dev/video0 2>&1 | tail -n +2 || true)"
  elif command -v fuser >/dev/null && fuser /dev/video0 >/dev/null 2>&1; then
    holder="$(fuser -v /dev/video0 2>&1 | tail -n +2 || true)"
  fi
  if [[ -n "${holder}" ]]; then
    echo "camera busy; not stealing it:" >&2
    echo "$holder" >&2
    echo "Stop that process, then retry. (Often viam-server on this box.)" >&2
    exit 1
  fi
fi

: > logs/mediamtx.log
: > logs/gst.log

nohup ./bin/mediamtx "$ROOT/mediamtx.yml" >> logs/mediamtx.log 2>&1 &
echo $! > run/mediamtx.pid
disown || true

for _ in $(seq 1 50); do
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

touch run/gst.loop
nohup "$ROOT/gst-loop.sh" >> logs/gst-loop.log 2>&1 &
echo $! > run/gst-loop.pid
disown || true

sleep 1
if [[ "$TESTSRC" != "1" ]] && grep -q "Device '/dev/video0' is busy" logs/gst.log; then
  echo "camera became busy after start; stopping." >&2
  "$ROOT/stop.sh" || true
  exit 1
fi

echo "preview running"
echo "  console: http://127.0.0.1:8090/"
echo "  direct:  http://100.101.94.5:8889/cam"
echo "  direct:  http://nvidia-3.tail40aa1c.ts.net:8889/cam"
echo "stop with: $ROOT/stop.sh"
