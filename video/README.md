# Video (WebRTC preview)

Lab camera path for teleop. **Code is not in this git repo yet**
(Feature F6). Product roadmap:
[`../IMPLEMENTATION.md`](../IMPLEMENTATION.md) (Features F6, F7, F8, F15, F16, F18).

## Where it lives today

| Place | Path |
| ----- | ---- |
| Orin `nvidia-3` (runs here) | `/home/nvidia/webrtc-preview-hassan` |
| Operator PC backup (not git) | `/home/muhammadhassan/tmp_dir2/webrtc-preview-hassan` |

Do not confuse this with viam on the Orin (port 8080). Do not put this
tree under teammate directories on the Orin.

## What it already does

```text
USB /dev/video0 (MJPG) or videotestsrc
  -> jpegdec (CPU) + nvvidconv (NVMM NV12)
  -> nvv4l2h264enc          # Jetson HW H.264  (this is already in the lab pipeline)
  -> rtph264pay mtu=1200
  -> UDP 127.0.0.1:5004
  -> MediaMTX
  -> Chrome WebRTC  TCP :8889  UDP :8189
```

Open on the operator PC:

- `http://100.101.94.5:8889/cam`
- `http://nvidia-3.tail40aa1c.ts.net:8889/cam`

Default 640×480 @ 30 fps, ~1.5 Mbps. `--720p` is 1280×720 @ ~2.5 Mbps.
`--testsrc` is SMPTE bars (no camera).

`start.sh` / `stop.sh` / `gst-publish.sh` / `mediamtx.yml` are in the
tree above. ICE is currently pinned to **Tailscale**
(`webrtcIPsFromInterfacesList: [tailscale0]`). That is a lab shortcut.
Tailscale stays SSH-only for the product data path (see locked
decisions in `IMPLEMENTATION.md`).

## Ports (Orin)

| Port | Use |
| ---- | --- |
| TCP 8889 | MediaMTX WebRTC / browser |
| UDP 8189 | WebRTC media |
| UDP 5004 | localhost RTP only (GStreamer → MediaMTX) |

## What is not done

- Tree is not in this repository
- Not in the operator web console (separate Chrome tab)
- ICE / media on Tailscale, not mlink
- `jpegdec` is software; NVENC is hardware (encode is HW, JPEG decode is not)
- Bitrate adapt, multi-cam, video-freshness watchdog: later features
- Encoder in use should be **confirmed** on the live Orin (`nvv4l2h264enc` in the pipeline is necessary but not the same as proving `/dev/v4l2-nvenc` is the one serving Chrome)

## Bring-up (lab)

```bash
ssh nvidia@nvidia-3
/home/nvidia/webrtc-preview-hassan/start.sh          # or --720p / --testsrc
# Chrome: http://100.101.94.5:8889/cam
/home/nvidia/webrtc-preview-hassan/stop.sh
```

If start says the camera is busy, another process holds `/dev/video0`
(often `viam-server`). Stop that first.
