#!/usr/bin/env bash
# Restart gst-publish.sh until run/gst.loop is removed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
while [[ -f "$ROOT/run/gst.loop" ]]; do
  echo "$(date -Is) gst-publish starting (RESOLUTION=${RESOLUTION:-640x480} TESTSRC=${TESTSRC:-0})" >> logs/gst.log
  "$ROOT/gst-publish.sh" >> logs/gst.log 2>&1 &
  echo $! > "$ROOT/run/gst.pid"
  wait "$(cat "$ROOT/run/gst.pid")" || true
  rm -f "$ROOT/run/gst.pid"
  [[ -f "$ROOT/run/gst.loop" ]] || break
  echo "$(date -Is) gst-publish exited, retry in 1s" >> logs/gst.log
  sleep 1
done
