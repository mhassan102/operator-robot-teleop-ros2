# Video (WebRTC preview)

Lab camera path for teleop. Product roadmap:
[`../IMPLEMENTATION.md`](../IMPLEMENTATION.md) (Features F6, F7, F8, F15, F16, F18).

F6 put this tree in git and plays the same stream in the operator
console at `http://127.0.0.1:8090/`.

## Where it runs

| Place | Path |
| ----- | ---- |
| Orin `nvidia-3` (camera computer) | `/home/nvidia/webrtc-preview-hassan` (lab copy) or this `video/` tree |
| This repo | `video/` (scripts + yaml; **not** the MediaMTX binary) |

Do not confuse this with viam on the Orin (port 8080). Do not put this
tree under teammate directories on the Orin.

## Pipeline

Decode / encode blocks (today vs F7 plan): [`decode-encode.md`](decode-encode.md).
F7 is **on hold**. Today: CPU `jpegdec`, HW `nvv4l2h264enc`. This
pipeline is Orin-only (`nvvidconv` / NVENC); no laptop software
fallback.

```text
USB /dev/video0 (MJPG) or videotestsrc
  -> jpegdec (CPU) + nvvidconv (NVMM NV12)
  -> nvv4l2h264enc          # Jetson HW H.264
  -> rtph264pay mtu=1200
  -> UDP 127.0.0.1:5004
  -> MediaMTX
  -> Chrome WebRTC  TCP :8889  UDP :8189
       operator console iframe  http://127.0.0.1:8090/
       or standalone            http://100.101.94.5:8889/cam
```

Default 640×480 @ 30 fps, ~1.5 Mbps. `--720p` is 1280×720 @ ~2.5 Mbps.
`--testsrc` is SMPTE bars (no camera).

ICE is pinned to **Tailscale** (`webrtcIPsFromInterfacesList:
[tailscale0]`). That is a lab shortcut so the current Orin preview
keeps working. F8 removes Tailscale from the video path. Do not add
new Tailscale dependencies. Tailscale stays SSH-only for the product
data path.

## MediaMTX binary (not in git)

The Orin binary is **linux/arm64 MediaMTX v1.20.1** (~61 MB). Do not
commit it. Download and place it at `video/bin/mediamtx` on the Orin:

```bash
curl -L -o /tmp/mediamtx.tar.gz \
  https://github.com/bluenviron/mediamtx/releases/download/v1.20.1/mediamtx_v1.20.1_linux_arm64.tar.gz
tar -xzf /tmp/mediamtx.tar.gz -C /tmp mediamtx
install -m 0755 /tmp/mediamtx /path/to/video/bin/mediamtx
```

The lab Orin already has this at
`/home/nvidia/webrtc-preview-hassan/bin/mediamtx`.

## Ports (Orin)

| Port | Use |
| ---- | --- |
| TCP 8889 | MediaMTX WebRTC / WHEP / browser |
| UDP 8189 | WebRTC media |
| UDP 5004 | localhost RTP only (GStreamer → MediaMTX) |

## Bring-up (lab)

On the operator PC, Tailscale must be up (`tailscale status` shows
`nvidia-3` online).

```bash
ssh nvidia@nvidia-3
# existing lab tree:
/home/nvidia/webrtc-preview-hassan/start.sh          # or --720p / --testsrc
# Chrome console (same stream):
#   http://127.0.0.1:8090/
# standalone player:
#   http://100.101.94.5:8889/cam
/home/nvidia/webrtc-preview-hassan/stop.sh
```

If start says the camera is busy, another process holds `/dev/video0`
(often `viam-server`). Stop that first.

The console camera panel uses MediaMTX **reader.js** (WHEP + trickle
ICE) against that `/cam` stream. Override with `?cam=` if the Orin
address differs:

```text
http://127.0.0.1:8090/?cam=http://100.101.94.5:8889/cam
```

Jog, heartbeat, HUD, and named poses are unchanged if the camera is
down. The panel shows `camera unavailable` if the iframe fails to
load.

## What is not done

- ICE / media still on Tailscale, not mlink (F8)
- `jpegdec` is software; NVENC is hardware (F7 **on hold** — see
  [`decode-encode.md`](decode-encode.md))
- Bitrate adapt, multi-cam, video-freshness watchdog: later features
