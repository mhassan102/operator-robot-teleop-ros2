# Camera decode / encode path (F7)

F7 is **on hold**. This file is the reference for what the Orin
pipeline does today vs what F7 would change. Product status:
[`../IMPLEMENTATION.md`](../IMPLEMENTATION.md) Feature F7.

The camera computer is the Orin. `gst-publish.sh` is Jetson-specific
(`nvvidconv` + `nvv4l2h264enc`). A laptop with no NVIDIA HW encode
does **not** get a software `x264enc` fallback while F7 is on hold.

**Same Orin chip, not the same engine.** JPEG decode is NVDEC/`nvjpeg`.
H.264 encode is **NVENC**. The CUDA GPU is not the codec.

Chrome always sees H.264. F7 only moves the JPEG unpack off the CPU,
if `nvjpegdec` exists on that L4T.

---

## Today (F7 on hold)

Software JPEG **decode** → hardware H.264 **encode**.

```text
 USB camera (/dev/video0)
        │  MJPG frames
        ▼
 ┌──────────────────┐
 │  jpegdec         │  DECODE  · CPU
 │  JPEG → pixels   │
 └────────┬─────────┘
          │ raw video
          ▼
 ┌──────────────────┐
 │  nvvidconv       │  format → NVMM NV12
 └────────┬─────────┘
          │
          ▼
 ┌──────────────────┐
 │  nvv4l2h264enc   │  ENCODE  · NVENC (HW)
 │  pixels → H.264  │
 └────────┬─────────┘
          │ RTP
          ▼
      MediaMTX  →  Chrome WebRTC
```

```text
  [camera MJPG] --CPU jpegdec--> [pixels] --NVENC--> [H.264] --> console
```

| Stage | Plugin | Job |
| ----- | ------ | --- |
| 1 Decode | `jpegdec` | JPEG → pixels (CPU) |
| 2 Encode | `nvv4l2h264enc` | pixels → H.264 for WebRTC (NVENC, already HW) |

`--testsrc` skips the USB/JPEG step but still uses `nvvidconv` +
`nvv4l2h264enc` (still Orin-only).

---

## F7 plan (not started)

Hardware JPEG **decode** → hardware H.264 **encode**. Same encode
step; only stage 1 changes.

```text
 USB camera (/dev/video0)
        │  MJPG frames
        ▼
 ┌──────────────────┐
 │  nvjpegdec       │  DECODE  · HW (nvjpeg / NVDEC)
 │  JPEG → pixels   │
 └────────┬─────────┘
          │ raw video
          ▼
 ┌──────────────────┐
 │  nvvidconv       │  format → NVMM NV12
 └────────┬─────────┘
          │
          ▼
 ┌──────────────────┐
 │  nvv4l2h264enc   │  ENCODE  · NVENC (HW)   ← already this today
 │  pixels → H.264  │
 └────────┬─────────┘
          │ RTP
          ▼
      MediaMTX  →  Chrome WebRTC
```

```text
  [camera MJPG] --HW nvjpegdec--> [pixels] --NVENC--> [H.264] --> console
```

| | Today | F7 plan |
| --- | --- | --- |
| Decode | `jpegdec` CPU | `nvjpegdec` HW |
| Encode | `nvv4l2h264enc` NVENC | unchanged |

F7 is **not** “if GPU then NVENC, else `x264enc`.” That would be a
new portable-encoder feature for a non-Jetson camera computer.
Do not add a CPU encoder “just in case.”
