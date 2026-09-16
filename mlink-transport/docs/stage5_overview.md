# Stage 5 — apps on 127.0.0.1 (F8)

mlink stays two host daemons (`mlink-op` on the operator PC, `mlink-edge`
on Orin nvidia-3). Apps never see WAN IPs. One session, two paths
(eth + wifi). Traffic class separates control from media.

```text
OPERATOR PC                                      ORIN
Chrome  http://127.0.0.1:8090/                   USB cam + nvv4l2h264enc
  HTTP/WS + local WebRTC                         robot ROS + Gazebo (headless)
        |                                         |
operator backend (Compose, host net)             robot UDP shim → /teleop/*
        | UDP 127.0.0.1  class=control            | UDP 127.0.0.1  class=control
        | UDP 127.0.0.1  class=media              | UDP 127.0.0.1  class=media
     mlink-op  ==== copies eth+wifi ====  mlink-edge
Tailscale = SSH only. No tailscale0. No 100.x in YAML.
```

## App ports (lab YAML)

| Side | Control in (`listen_app`) | Control out (`send_app`) | Media |
| ---- | ------------------------- | ------------------------ | ----- |
| op   | 127.0.0.1:5501            | 127.0.0.1:5502           | `send_media` 127.0.0.1:5004 (MediaMTX RTP) |
| edge | 127.0.0.1:5503            | 127.0.0.1:5504           | `listen_media` 127.0.0.1:5004 (gst RTP) |

Loopback YAML omits media ports; `mlink-ping` is unchanged.

## Control payload

Documented compact binary in
`teleoperation-prototype/.../teleop_demo/mlink_payload.py` (not ROS CDR).
Types: command, heartbeat, ack, state, tool pose, named-pose req/rep.
MTU budget 1440. Named poses use the same mux.

Zenoh stays on-host inside the robot container. Do not put `rmw_zenoh`
TCP 7447 through mlink.

## Media

Orin gst (`nvv4l2h264enc` + `rtph264pay` mtu=1200) → mlink media class →
operator MediaMTX on 127.0.0.1:5004 → WHEP `http://127.0.0.1:8889/cam`.
Browser ICE is localhost only.

## Run

See [`usage.md`](usage.md) Stage 5.
