# F8 usage (mlink Stage 5)

How to bring up the F8 two-host path. Feature **F8 is done** (lab
cable-pull tested). Product roadmap:
[`../../IMPLEMENTATION.md`](../../IMPLEMENTATION.md) (Feature F8).
mlink contract: [`../README.md`](../README.md) Stage 5. Camera notes:
[`../../video/README.md`](../../video/README.md).

## What F8 is

mlink stays two host daemons (`mlink-op` on this PC, `mlink-edge` on the
Orin). Operator backend and robot ROS talk opaque UDP on `127.0.0.1`.
mlink copies those datagrams on Ethernet + Wi-Fi. Tailscale is SSH only.

| Host | Process |
| --- | --- |
| Operator PC | `mlink-op`, operator Compose (`compose.operator-mlink.yaml`), MediaMTX player, Chrome `http://127.0.0.1:8090/` |
| Orin `nvidia-3` | `mlink-edge`, robot Compose (`compose.robot-mlink.yaml`, Gazebo headless), gst camera → RTP `127.0.0.1:5004` |

Orin work tree: `/home/nvidia/hassan`.

The console camera is the **real USB** view. There is no simulated arm in
that picture (no SO-101 in front of the lens). HUD tool pose / optional
VNC of Gazebo is how you see the sim move. VNC is not part of this
bring-up (headless default).

Single-host Zenoh (`./scripts/start.sh` + `test_*.sh`) is **not** the F8
path. Keep those for localhost only.

---

## 0. SSH and IPs

SSH the Orin on **Wi-Fi** so an Ethernet pull does not kill the session:

```bash
ssh nvidia@192.168.223.44
```

Guest DHCP moves. Re-check before every run. Last YAML values:

| Side | if | addr |
| --- | --- | --- |
| Operator | `enx00e04c681cc3` | `192.168.108.1` |
| Operator | `wlo1` | `192.168.222.9` |
| Orin | `eno1` | `192.168.108.120` |
| Orin | `wlP1p1s0` | `192.168.223.44` |

```bash
# operator PC
ip -br addr
ip route | grep '^default'    # must stay on wlo1

# Orin (Wi-Fi SSH)
ssh nvidia@192.168.223.44 'ip -br addr; ip route | grep "^default"'
```

If `wlo1` / `wlP1p1s0` moved, edit the **wifi** `bind_ip` / `peer:` in both:

- `mlink-transport/config/lab-op.yaml`
- `mlink-transport/config/lab-edge.yaml`

Do not use `100.x` or `tailscale0`. Then rsync mlink YAML again (step 1).

---

## 1. Copy trees to the Orin

From repo root on the operator PC
(`/home/muhammadhassan/robots`):

```bash
rsync -az --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' \
  mlink-transport/ nvidia@192.168.223.44:/home/nvidia/hassan/mlink-transport/

rsync -az --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' \
  --exclude 'ros2_ws/build' --exclude 'ros2_ws/install' --exclude 'ros2_ws/log' \
  teleoperation-prototype/ nvidia@192.168.223.44:/home/nvidia/hassan/teleoperation-prototype/

rsync -az --exclude 'bin/mediamtx' --exclude 'logs' --exclude 'run' \
  video/ nvidia@192.168.223.44:/home/nvidia/hassan/video/
```

Orin image `ros2-teleop-poc:humble` and workspace were already built on
`nvidia-3` during implementation. Rebuild only if Docker or source
changed after that:

```bash
ssh nvidia@192.168.223.44
cd /home/nvidia/hassan/teleoperation-prototype
docker compose -f compose.robot-mlink.yaml build    # first time / Dockerfile change
./scripts/build_workspace_mlink.sh robot            # after Python/launch changes
```

---

## 2. Free Orin UDP 5004

Stop the old F6 MediaMTX so `listen_media` can bind. It is currently
held by `/home/nvidia/webrtc-preview-hassan`.

```bash
ssh nvidia@192.168.223.44
/home/nvidia/webrtc-preview-hassan/stop.sh || true
```

---

## 3. Start mlink (before apps / camera)

Keep the Orin SSH on Wi-Fi.

```bash
# 1. Orin — mlink-edge
ssh nvidia@192.168.223.44
cd /home/nvidia/hassan/mlink-transport
PYTHONPATH=. python3 -m edge --config config/lab-edge.yaml
```

```bash
# 2. Operator PC — mlink-op
cd /home/muhammadhassan/robots/mlink-transport
PYTHONPATH=. python3 -m op --config config/lab-op.yaml --control 127.0.0.1:5510
```

---

## 4. Start robot (Orin) and operator (this PC)

```bash
# 3. Orin — Gazebo sim, headless (TELEOP_GAZEBO_GUI=false)
ssh nvidia@192.168.223.44
cd /home/nvidia/hassan/teleoperation-prototype
./scripts/start_robot_mlink.sh
```

```bash
# 4. Operator PC — backend + console (HTTP 127.0.0.1:8090)
cd /home/muhammadhassan/robots/teleoperation-prototype
./scripts/build_workspace_mlink.sh operator   # once on this PC after F8 source
./scripts/start_operator_mlink.sh
```

---

## 5. Camera into mlink media (not Tailscale)

```bash
# 5. Orin — gst nvv4l2h264enc → RTP 127.0.0.1:5004 (mlink-edge listen_media)
ssh nvidia@192.168.223.44
cd /home/nvidia/hassan/video
./start.sh          # or --testsrc / --720p
# if "camera busy", stop viam-server (or whatever holds /dev/video0)
```

```bash
# 6. Operator PC — localhost MediaMTX player (ICE 127.0.0.1 only)
cd /home/muhammadhassan/robots/video
./start-player.sh   # uses video/bin/mediamtx linux_amd64
```

Open **http://127.0.0.1:8090/**

Look for:

- Camera live (real USB view; no simulated arm in that picture)
- HUD: connection / watchdog / session / tool pose
- Keys jog the Gazebo arm (HUD pose moves)
- Named-pose buttons use the same UDP mux (same protocol; still bypasses
  the 500 ms watchdog until F10)

---

## 6. Cable-pull (F8 acceptance)

Operator PC. Keep the Orin SSH on Wi-Fi.

```bash
nmcli device disconnect enx00e04c681cc3
```

Expect: camera stays, keys still jog, HUD still updates.

```bash
ssh nvidia@192.168.223.44 'echo still-here'
```

Bring Ethernet back:

```bash
nmcli connection up "Wired connection 1"
```

---

## 7. Stop

```bash
# operator PC
cd /home/muhammadhassan/robots/video && ./stop.sh
cd /home/muhammadhassan/robots/teleoperation-prototype && ./scripts/stop_mlink.sh
# Ctrl-C mlink-op
```

```bash
# Orin (Wi-Fi SSH)
cd /home/nvidia/hassan/video && ./stop.sh
cd /home/nvidia/hassan/teleoperation-prototype && ./scripts/stop_mlink.sh
# Ctrl-C mlink-edge
```

---

## Localhost tests (not F8)

`./scripts/start.sh` plus `test_basic.sh` / `test_delivery.sh` /
`test_watchdog.sh` / `test_console_session.sh` /
`test_console_named_pose.sh` / `test_sim.sh` / `test_named_pose.sh`
still run **both containers on one host** via Zenoh. They do not
exercise mlink. F8 acceptance is the two-host cable-pull above.

mlink unit tests (operator PC):

```bash
cd /home/muhammadhassan/robots/mlink-transport && python3 -m pytest
```

---

## Out of this test

- F7 (Orin HW JPEG decode), F9 (5G / `wwan0`), F10 Safety-A
- F12 product packaging, F13–F21
- VNC of Gazebo (headless default)
- git commit / push / merge
- marking F8 `STATUS: done` in `IMPLEMENTATION.md`
