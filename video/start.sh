#!/usr/bin/env bash
# Orin camera host: USB (or testsrc) -> nvv4l2h264enc -> RTP 127.0.0.1:5004
# into mlink-edge listen_media. Does not start MediaMTX (that is start-player.sh
# on the operator PC).
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
      echo "  default: /dev/video0 640x480 MJPG -> NVENC H.264 -> RTP 127.0.0.1:5004"
      echo "  --720p    1280x720"
      echo "  --testsrc SMPTE bars (does not use the USB camera)"
      echo
      echo "mlink-edge must already be listening on 127.0.0.1:5004 (listen_media)."
      echo "Operator player: video/start-player.sh then http://127.0.0.1:8090/"
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done
export RESOLUTION TESTSRC

if [[ -f run/gst.pid ]] && kill -0 "$(cat run/gst.pid)" 2>/dev/null; then
  echo "already running (pid $(cat run/gst.pid)). ./stop.sh first." >&2
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

: > logs/gst.log
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

echo "camera encode running -> UDP 127.0.0.1:${UDP_PORT:-5004} (mlink-edge listen_media)"
echo "  operator player: video/start-player.sh"
echo "  console:         http://127.0.0.1:8090/"
echo "stop with: $ROOT/stop.sh"
