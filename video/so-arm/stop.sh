#!/usr/bin/env bash
# Stop the SO-ARM camera launcher only. Does not stop video/start.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

kill_pidfile() {
  local f="$1"
  if [[ -f "$f" ]]; then
    local p
    p="$(cat "$f" || true)"
    if [[ -n "${p}" ]] && kill -0 "$p" 2>/dev/null; then
      kill "$p" 2>/dev/null || true
      for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$p" 2>/dev/null || break
        sleep 0.1
      done
      kill -9 "$p" 2>/dev/null || true
    fi
    rm -f "$f"
  fi
}

kill_pidfile run/gst.pid
kill_pidfile run/mediamtx.pid
echo "stopped"
