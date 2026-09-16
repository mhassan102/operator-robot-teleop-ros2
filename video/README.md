# Video (WebRTC preview)

Lab camera path for teleop. Product roadmap:
[`../IMPLEMENTATION.md`](../IMPLEMENTATION.md) (Features F6, F7, F8, F15, F16, F18).

F8 sends RTP through mlink (`media` class). The operator console at
`http://127.0.0.1:8090/` plays WebRTC from **localhost MediaMTX**, not
from Tailscale ICE on the Orin.

## Where it runs

| Place | Path |
| ----- | ---- |
| Orin `nvidia-3` (camera + encode) | this `video/` tree: `./start.sh` (gst only) |
| Operator PC (player) | this `video/` tree: `./start-player.sh` (MediaMTX) |
| This repo | `video/` (scripts + yaml; **not** the MediaMTX binary) |

Do not confuse this with viam on the Orin (port 8080). Do not put this
tree under teammate directories on the Orin.

## Pipeline (F8)

```text
USB /dev/video0 (MJPG) or videotestsrc
  -> jpegdec (CPU) + nvvidconv (NVMM NV12)
  -> nvv4l2h264enc          # Jetson HW H.264
  -> rtph264pay mtu=1200
  -> UDP 127.0.0.1:5004     # mlink-edge listen_media
  -> mlink copies eth+wifi
  -> mlink-op send_media 127.0.0.1:5004
  -> MediaMTX on the operator PC
  -> Chrome WHEP  http://127.0.0.1:8889/cam
       operator console  http://127.0.0.1:8090/
```

Default 640×480 @ 30 fps, ~1.5 Mbps. `--720p` is 1280×720 @ ~2.5 Mbps.
`--testsrc` is SMPTE bars (no camera).

ICE is **localhost only** (`webrtcAdditionalHosts: ["127.0.0.1"]`,
listener `127.0.0.1:8889`). Tailscale is SSH only. The old Orin-hosted
Tailscale ICE file is [`mediamtx.tailscale-lab.yml`](mediamtx.tailscale-lab.yml)
(not the product path).

## MediaMTX binary (not in git)

Operator PC needs **linux/amd64 MediaMTX v1.20.1**. Place it at
`video/bin/mediamtx`:

```bash
curl -L -o /tmp/mediamtx.tar.gz \
  https://github.com/bluenviron/mediamtx/releases/download/v1.20.1/mediamtx_v1.20.1_linux_amd64.tar.gz
tar -xzf /tmp/mediamtx.tar.gz -C /tmp mediamtx
install -m 0755 /tmp/mediamtx video/bin/mediamtx
```

The Orin lab copy at `/home/nvidia/webrtc-preview-hassan/bin/mediamtx`
is linux/arm64 and is **not** used for F8 (MediaMTX runs on the
operator PC). Encoder gst still runs on the Orin.

## Ports

| Where | Port | Use |
| ----- | ---- | --- |
| Orin host | UDP 5004 | gst → mlink-edge `listen_media` |
| Operator host | UDP 5004 | mlink-op `send_media` → MediaMTX RTP |
| Operator host | TCP 127.0.0.1:8889 | MediaMTX WebRTC / WHEP |
| Operator host | UDP 127.0.0.1:8189 | WebRTC ICE (localhost) |

## Bring-up (F8)

mlink-edge must be up on the Orin **before** `./start.sh` so 5004 is
the mlink app face, not a leftover MediaMTX.

```bash
# Orin (Wi-Fi SSH). mlink-edge already running.
cd /home/nvidia/hassan/video
./start.sh          # or --720p / --testsrc

# Operator PC. mlink-op already running.
cd video
./start-player.sh
# Chrome: http://127.0.0.1:8090/
```

If start on the Orin says the camera is busy, another process holds
`/dev/video0` (often `viam-server`). Stop that first.

Override the console camera URL with `?cam=` if needed (default is
localhost):

```text
http://127.0.0.1:8090/?cam=http://127.0.0.1:8889/cam
```

Jog, heartbeat, HUD, and named poses are unchanged if the camera is
down. The panel shows `camera unavailable` if WHEP fails.

Stop both sides with `./stop.sh` in this directory.

## What is not done

- `jpegdec` is software; NVENC is hardware (F7 **on hold** — see
  [`decode-encode.md`](decode-encode.md))
- Bitrate adapt, multi-cam, video-freshness watchdog: later features
