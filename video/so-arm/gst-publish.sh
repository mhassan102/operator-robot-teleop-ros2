#!/usr/bin/env bash
# USB camera MJPEG -> software x264enc -> RTP 127.0.0.1:5004 for MediaMTX.
# Software encoder on the SO-ARM laptop. The Orin tree keeps its own encoder.
set -euo pipefail

DEVICE="${DEVICE:-/dev/video2}"
WIDTH="${WIDTH:-640}"
HEIGHT="${HEIGHT:-480}"
FPS="${FPS:-30}"
BITRATE="${BITRATE:-1500}"
HOST="${UDP_HOST:-127.0.0.1}"
PORT="${UDP_PORT:-5004}"

if [[ "${SO_ARM_CAMERA_PARSE_ONLY:-}" == "1" || "${SO_ARM_CAMERA_FORBID_START:-}" == "1" ]]; then
  echo "ERROR: gst-publish refused to open ${DEVICE}" >&2
  exit 99
fi

exec gst-launch-1.0 -e \
  v4l2src device="${DEVICE}" do-timestamp=true \
  ! "image/jpeg,width=${WIDTH},height=${HEIGHT},framerate=${FPS}/1" \
  ! jpegdec \
  ! videoconvert \
  ! x264enc tune=zerolatency speed-preset=ultrafast bitrate="${BITRATE}" key-int-max=15 byte-stream=true \
  ! "video/x-h264,profile=baseline" \
  ! rtph264pay config-interval=1 pt=96 mtu=1200 \
  ! udpsink host="${HOST}" port="${PORT}" sync=false async=false
