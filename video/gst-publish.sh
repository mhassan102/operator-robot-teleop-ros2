#!/usr/bin/env bash
# Capture USB camera (or test pattern) -> Jetson HW H.264 -> RTP/UDP for MediaMTX.
set -euo pipefail

DEVICE="${DEVICE:-/dev/video0}"
WIDTH="${WIDTH:-640}"
HEIGHT="${HEIGHT:-480}"
FPS="${FPS:-30}"
BITRATE="${BITRATE:-1500000}"
HOST="${UDP_HOST:-127.0.0.1}"
PORT="${UDP_PORT:-5004}"

if [[ "${1:-}" == "720p" || "${RESOLUTION:-}" == "720p" ]]; then
  WIDTH=1280
  HEIGHT=720
  BITRATE="${BITRATE_720P:-2500000}"
fi

if [[ "${TESTSRC:-0}" == "1" ]]; then
  exec gst-launch-1.0 -e \
    videotestsrc is-live=true pattern=smpte \
    ! "video/x-raw,width=${WIDTH},height=${HEIGHT},framerate=${FPS}/1" \
    ! nvvidconv \
    ! "video/x-raw(memory:NVMM),format=NV12" \
    ! nvv4l2h264enc insert-sps-pps=true insert-vui=true iframeinterval=15 idrinterval=15 bitrate="${BITRATE}" preset-level=1 profile=0 disable-cabac=true maxperf-enable=true \
    ! rtph264pay config-interval=1 pt=96 mtu=1200 \
    ! tee name=t \
    t. ! queue ! udpsink host="${HOST}" port="${PORT}" sync=false async=false \
    t. ! queue ! udpsink host="127.0.0.1" port="5005" sync=false async=false
fi

exec gst-launch-1.0 -e \
  v4l2src device="${DEVICE}" do-timestamp=true \
  ! "image/jpeg,width=${WIDTH},height=${HEIGHT},framerate=${FPS}/1" \
  ! jpegdec \
  ! nvvidconv \
  ! "video/x-raw(memory:NVMM),format=NV12" \
  ! nvv4l2h264enc insert-sps-pps=true insert-vui=true iframeinterval=15 idrinterval=15 bitrate="${BITRATE}" preset-level=1 profile=0 disable-cabac=true maxperf-enable=true \
  ! rtph264pay config-interval=1 pt=96 mtu=1200 \
  ! tee name=t \
  t. ! queue ! udpsink host="${HOST}" port="${PORT}" sync=false async=false \
  t. ! queue ! udpsink host="127.0.0.1" port="5005" sync=false async=false
