# mlink-transport — how to run

Userspace UDP bonding (LLTP-like). v1 sends a copy of each datagram on
every live path. The receiver keeps the first good copy and drops the
rest. No Linux default-route failover. The Orin lab path does not use
Tailscale as a data path. F18 adds an explicit `allow_tailscale: true`
session for this PC ↔ the SO-ARM laptop only (Stage 3 path smoke,
Stage 4 console g/h).

Stage plan (default doc): [`../README.md`](../README.md).
Product roadmap: [`../../IMPLEMENTATION.md`](../../IMPLEMENTATION.md).

Stage 1 is the **protocol library and unit tests** (fake clock, fake
sockets). Stage 2 is **two OS processes on localhost** (`mlink-op` /
`mlink-edge`) plus `mlink-ping`. Stage 3 is the same daemons on **real
Ethernet + Wi-Fi** between this PC and Orin `nvidia-3`, with
`SO_BINDTODEVICE` when `ifname` is set.

## Run tests

From this directory (Python 3.10+, Ubuntu x86_64 or Orin aarch64).
Needs `pytest` and `PyYAML` (`pip install -r requirements.txt`).

```bash
cd mlink-transport
python3 -m pytest
```

## Stage 2 loopback demo

Two processes on this PC. Two UDP port-pairs pretend to be eth and wifi
(`config/loopback.yaml` + `config/loopback-edge.yaml`). No Orin, no
recable, no Tailscale data path.

From this directory, three terminals:

```bash
# 1. operator daemon — binds 41001/41002, app 127.0.0.1:5501/5502
python3 -m op --config config/loopback.yaml --control 127.0.0.1:5510
```

```bash
# 2. edge daemon — binds 42001/42002; --reflect echoes payloads back
python3 -m edge --config config/loopback-edge.yaml --reflect
```

```bash
# 3. 1000 datagrams into op listen_app; kill the local eth path mid-run
python3 -m ping --count 1000 --kill-after 400 --kill-path eth --control 127.0.0.1:5510
```

`mlink-ping` prints `sent=… delivered=… loss=… max_gap_ms=…` and a JSON
line. Killing eth closes that localhost port-pair **inside mlink** (not
iptables). The stream continues on wifi.

To exercise the edge app face instead of `--reflect` on the daemon, in
place of terminal 2 run edge without `--reflect` and add a reflector:

```bash
python3 -m edge --config config/loopback-edge.yaml
python3 -m ping --reflect --bind 127.0.0.1:5504 --target 127.0.0.1:5503
```

Stop the daemons with Ctrl-C.

Ports, both processes, and the ping round-trip: [`stage2_overview.md`](stage2_overview.md).

## Stage 3 two-machine demo (Ethernet + Wi-Fi)

Operator PC ↔ Orin `nvidia-3`. Copies go out both NICs. First good copy
wins. **Do not** change the Linux default route (keep it on Wi-Fi).
**Do not** use Tailscale as a data path (SSH only).

Live IPs live in `config/lab-op.yaml` / `config/lab-edge.yaml` and must
match `ip -br addr` on each machine.

### Ethernet IPs (no default-route change)

Cable: operator USB-Ethernet `enx00e04c681cc3` ↔ Orin `eno1`. Use a
private `/24` on that cable only (lab: `192.168.108.0/24`). Do **not**
put Ethernet on the Guest Wi-Fi subnet. Do **not** add a default via
the dongle.

```bash
# operator — address the dongle; do NOT add a default via this NIC
sudo ip addr add 192.168.108.1/24 dev enx00e04c681cc3
sudo ip link set enx00e04c681cc3 up
ip route | grep '^default'    # must stay: default via … dev wlo1

# Orin nvidia-3
sudo ip addr add 192.168.108.120/24 dev eno1
sudo ip link set eno1 up
ip route | grep '^default'    # must stay: default via … dev wlP1p1s0
```

If NetworkManager already assigned those IPs, skip the `ip addr add`.
Confirm Guest Wi-Fi still pings (this PC `wlo1` ↔ Orin `192.168.223.44`).
If `python3 -m op` fails with `Cannot assign requested address`, Wi-Fi
DHCP moved: `ip -br addr` and update `bind_ip` in `lab-op.yaml` plus
`peer:` on the wifi path in `lab-edge.yaml`.
and SSH still works: `ssh nvidia@192.168.223.44` (Wi-Fi) and
`ssh nvidia@192.168.108.120` (Ethernet). Tailscale SSH is a third
management path only.

### Copy tree to Orin and run

From the repo root on the operator PC:

```bash
rsync -az --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' \
  mlink-transport/ nvidia@192.168.108.120:/home/nvidia/hassan/mlink-transport/
```

Three terminals (two here, one on the Orin):

```bash
# On Orin (SSH over Wi-Fi so the session survives an Ethernet pull)
ssh nvidia@192.168.223.44
cd /home/nvidia/hassan/mlink-transport
PYTHONPATH=. python3 -m edge --config config/lab-edge.yaml --reflect
```

```bash
# Operator PC — mlink-op
cd mlink-transport
PYTHONPATH=. python3 -m op --config config/lab-op.yaml --control 127.0.0.1:5510
```

```bash
# Operator PC — 1000 datagrams, then a ~1 Mbps dummy stream, or until Ctrl-C
cd mlink-transport
PYTHONPATH=. python3 -m ping --count 1000 --interval-ms 2
# ~1 Mbps: 1250 B × 100/s × 8 = 1e6 bit/s for ~20 s
PYTHONPATH=. python3 -m ping --count 2000 --interval-ms 10 --payload-size 1250
# live forever (status line every 1 s); Ctrl-C to stop
PYTHONPATH=. python3 -m ping --count 0 --interval-ms 20
```

Daemon logs `path_id` first-good winners (`win=` in the one-second
`stats` line; `first-good seq=… path=… path_id=…` at DEBUG). Proof of
both NICs is that log plus `tcpdump -ni enx00e04c681cc3 udp port 46000`
and `tcpdump -ni wlo1 udp port 46000` (and the same on Orin `eno1` /
`wlP1p1s0`).

### Cable pull

While the ~1 Mbps ping is running, unplug the USB-Ethernet cable (or,
from the operator PC, drop only that NIC — not Wi-Fi):

```bash
nmcli device disconnect enx00e04c681cc3
```

The stream must continue on Wi-Fi (`wifi=up`, `eth=down`, small
`max_gap_ms`). SSH over Guest Wi-Fi / Tailscale must stay up:

```bash
ssh nvidia@192.168.223.44 'echo still-here'
```

Bring Ethernet back after the run:

```bash
nmcli connection up "Wired connection 1"
```

Do not `ip route` anything. Do not bind `tailscale0`. `SO_BINDTODEVICE`
may need `CAP_NET_ADMIN` (or `CAP_NET_RAW` on older kernels). This lab
allows it unprivileged; if you get EPERM:

```bash
sudo setcap cap_net_admin,cap_net_raw+ep "$(readlink -f "$(command -v python3)")"
```

Leave `ifname` unset (loopback YAML) and the factory is the Stage 2
plain bind.

Topology: [`stage3_overview.md`](stage3_overview.md).

## F18 Stage 3 — operator PC ↔ SO-ARM laptop (Tailscale opt-in)

One UDP path on `tailscale0` between this PC and the SO-ARM laptop.
Orin `lab-op.yaml` / `lab-edge.yaml` stay the eth+wifi pair and still
load without the flag. This pair is **control only** (no media through
mlink). Camera stays on the laptop MediaMTX URL
`http://100.67.47.79:8889/cam` (`?cam=` on the console). Stage 3 is
**path smoke only** (`--reflect` + ping). Do **not** start robot
Compose / `--real-arm` here. End-to-end g/h is F18 Stage 4 below.

Confirm IPs before editing YAML (`tailscale ip -4`):

| Host | Tailscale name | `tailscale0` |
| --- | --- | --- |
| Operator PC | `pure-dev-muhammadhassan` | `100.95.150.54` |
| SO-ARM laptop | `gt-dev-muhammadusama` | `100.67.47.79` |

Existing daemon CLI: `python3 -m op --config …` / `python3 -m edge --config …`.

From the repo root on this PC:

```bash
rsync -az --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' \
  mlink-transport/ usama@gt-dev-muhammadusama:/home/usama/hassan/mlink-transport/
```

Keep `TELEOP_ARM` / robot Compose **stopped** on the laptop (Stage 2
driver is present; mlink + a stray command could move the gripper).

```bash
# Laptop — mlink-edge (control-only; --reflect for ping/heartbeat smoke)
ssh usama@gt-dev-muhammadusama
cd /home/usama/hassan/mlink-transport
PYTHONPATH=. python3 -m edge --config config/lab-edge-remote-laptop.yaml --reflect
```

```bash
# Operator PC — mlink-op
cd /home/muhammadhassan/robots/mlink-transport
PYTHONPATH=. python3 -m op --config config/lab-op-remote-laptop.yaml --control 127.0.0.1:5510
```

Daemon logs `session=1` and a one-second `stats` line (`ts=up` plus
RTT once heartbeats echo). Optional ping (still no arm motion):

```bash
cd /home/muhammadhassan/robots/mlink-transport
PYTHONPATH=. python3 -m ping --count 20 --interval-ms 50
```

Stop both daemons with Ctrl-C. Do not run `test_real_gripper.sh` in
this stage. `SO_BINDTODEVICE` on `tailscale0` may need `CAP_NET_ADMIN`
(same as Stage 3 Ethernet).

## F18 Stage 4 — console g/h → Tailscale mlink → Feetech gripper

Operator on this PC opens the console, watches the laptop camera, and
taps **g** / **h** once. Commands ride mlink (Tailscale, control-only).
Camera stays on MediaMTX; it is **not** sent through mlink. No Gazebo,
no named poses, no MoveIt Servo, no joints 1–5.

`--remote-laptop` picks `lab-*-remote-laptop.yaml`. Default remains
Orin `lab-op.yaml` / `lab-edge.yaml`. `start_robot_mlink.sh` default
is still Gazebo; use `--real-arm` on the laptop. Do **not** pass
`--reflect` on edge (that echoes; it will not drive the robot). Do
**not** pass `--control` on op (that is ping).

Confirm IPs (`tailscale ip -4`) match the table in F18 Stage 3.

From the repo root on this PC, copy trees:

```bash
rsync -az --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' \
  mlink-transport/ usama@gt-dev-muhammadusama:/home/usama/hassan/mlink-transport/

rsync -az --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' \
  --exclude 'ros2_ws/build' --exclude 'ros2_ws/install' --exclude 'ros2_ws/log' \
  teleoperation-prototype/ \
  usama@gt-dev-muhammadusama:/home/usama/hassan/teleoperation-prototype/
```

Laptop first-time image/workspace (skip if Stage 2 already built):

```bash
ssh usama@gt-dev-muhammadusama
cd /home/usama/hassan/teleoperation-prototype
docker compose -f compose.robot-mlink.yaml -f compose.robot-mlink.real-arm.yaml build
./scripts/build_workspace_mlink.sh robot
```

This PC, after F18 source lands:

```bash
cd /home/muhammadhassan/robots/teleoperation-prototype
./scripts/build_workspace_mlink.sh operator
```

### Bring-up order

Clear the gripper. Watch the camera **before** any key. One tap, not a
held key. Do not mash g/h to “catch up” if the path flaps.

```bash
# 1. Laptop camera (existing MediaMTX; not through mlink)
ssh usama@gt-dev-muhammadusama
/home/usama/teleops_hassan/video/start.sh
```

```bash
# 2. Laptop mlink-edge WITHOUT --reflect
ssh usama@gt-dev-muhammadusama
cd /home/usama/hassan/mlink-transport
./scripts/start_daemon.sh edge --remote-laptop
```

```bash
# 3. This PC mlink-op WITHOUT --control
cd /home/muhammadhassan/robots/mlink-transport
./scripts/start_daemon.sh op --remote-laptop
```

Daemon logs `session=1` and a one-second `stats` line (`ts=up`). App
ports: op `127.0.0.1:5501/5502`, edge `127.0.0.1:5503/5504`.

```bash
# 4. Laptop robot --real-arm (TELEOP_MLINK=1, Feetech id 6, max_delta 48)
ssh usama@gt-dev-muhammadusama
cd /home/usama/hassan/teleoperation-prototype
./scripts/start_robot_mlink.sh --real-arm
```

```bash
# 5. This PC operator backend
cd /home/muhammadhassan/robots/teleoperation-prototype
./scripts/start_operator_mlink.sh --remote-laptop
```

```text
# 6. Console. Heartbeat must keep CONNECTED before any key.
http://127.0.0.1:8090/?cam=http://100.67.47.79:8889/cam
```

7. One **g** (tiny open) or one **h** (tiny close). Watch the camera.
   Do not press wasd. Then stop.

If joints 1–5 twitch, stop immediately.

### Soft stop

USB unplug is not available.

```bash
# this PC
cd /home/muhammadhassan/robots/teleoperation-prototype
./scripts/stop_mlink.sh
# Ctrl-C mlink-op

# laptop
ssh usama@gt-dev-muhammadusama
cd /home/usama/hassan/teleoperation-prototype && ./scripts/stop_mlink.sh
# Ctrl-C mlink-edge
/home/usama/teleops_hassan/video/stop.sh
```

Closing the console tab (or stopping operator Compose) must trip the
500 ms watchdog and Feetech deadman torque-off. Path flap (~1 s ts
down) must also deadman; do not mash g/h.

### Must not

- `--reflect` on edge
- Cartesian jog (wasd) on the real arm
- Named poses
- EEPROM / servo IDs 1–5
- Changing `allow_tailscale` default (Orin YAML still omits it)
- Video through mlink

## Stage 5 teleop over mlink (Ethernet + Wi-Fi)

Operator PC: `mlink-op`, operator Compose, MediaMTX player, Chrome
`http://127.0.0.1:8090/`. Orin: `mlink-edge`, robot Compose (Gazebo
headless), gst `nvv4l2h264enc` → RTP 127.0.0.1:5004. Tailscale is SSH
only. Default route stays on Wi-Fi.

SSH the Orin over Wi-Fi so an Ethernet pull does not kill the session:
`ssh nvidia@192.168.223.44`.

Live bind/peer IPs are in `config/lab-op.yaml` / `config/lab-edge.yaml`.
Guest DHCP moves; re-check before start:

```bash
# operator PC
ip -br addr
ip route | grep '^default'    # must stay on wlo1

# Orin (Wi-Fi SSH)
ssh nvidia@192.168.223.44 'ip -br addr; ip route | grep "^default"'
```

If Wi-Fi IPs moved, edit `bind_ip` / `peer:` on the `wifi` path in both
YAML files. Do not use `100.x` or `tailscale0`.

Copy trees (from the repo root on the operator PC):

```bash
rsync -az --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' \
  mlink-transport/ nvidia@192.168.223.44:/home/nvidia/hassan/mlink-transport/

rsync -az --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' \
  --exclude 'ros2_ws/build' --exclude 'ros2_ws/install' --exclude 'ros2_ws/log' \
  teleoperation-prototype/ nvidia@192.168.223.44:/home/nvidia/hassan/teleoperation-prototype/

rsync -az --exclude 'bin/mediamtx' --exclude 'logs' --exclude 'run' \
  video/ nvidia@192.168.223.44:/home/nvidia/hassan/video/
```

On Orin, first time only: build the **aarch64** image there (do not copy
an x86 image), then colcon-build the mounted workspace:

```bash
ssh nvidia@192.168.223.44
cd /home/nvidia/hassan/teleoperation-prototype
docker compose -f compose.robot-mlink.yaml build
./scripts/build_workspace_mlink.sh robot
```

Stop the old F6 Orin MediaMTX first so UDP 5004 is free for
`mlink-edge` `listen_media` (gst still sends to 127.0.0.1:5004):

```bash
ssh nvidia@192.168.223.44
# old lab tree, if it is running:
/home/nvidia/webrtc-preview-hassan/stop.sh || true
```

Bring-up order (mlink before apps, camera after mlink-edge):

```bash
# 1. Orin — mlink-edge (Wi-Fi SSH)
ssh nvidia@192.168.223.44
cd /home/nvidia/hassan/mlink-transport
PYTHONPATH=. python3 -m edge --config config/lab-edge.yaml
```

```bash
# 2. Operator PC — mlink-op
cd mlink-transport
PYTHONPATH=. python3 -m op --config config/lab-op.yaml --control 127.0.0.1:5510
```

```bash
# 3. Orin — robot container (Gazebo headless)
ssh nvidia@192.168.223.44
cd /home/nvidia/hassan/teleoperation-prototype
./scripts/start_robot_mlink.sh
```

```bash
# 4. Operator PC — backend + console
cd teleoperation-prototype
./scripts/build_workspace_mlink.sh operator   # once after F8 source lands
./scripts/start_operator_mlink.sh
```

```bash
# 5. Orin — camera into mlink media (not Tailscale, not MediaMTX on Orin)
ssh nvidia@192.168.223.44
# if start says the camera is busy, stop viam-server (or whatever holds /dev/video0)
cd /home/nvidia/hassan/video
./start.sh          # or --testsrc / --720p
```

```bash
# 6. Operator PC — localhost WebRTC player
cd video
./start-player.sh   # needs video/bin/mediamtx linux_amd64
```

Open `http://127.0.0.1:8090/`. Camera is the real USB view. HUD
connection/watchdog/pose come over mlink control. Keys jog the Gazebo
arm on the Orin (tool pose in the HUD moves; the camera picture has no
simulated arm).

Cable pull (operator PC). Keep the Orin SSH on Wi-Fi:

```bash
nmcli device disconnect enx00e04c681cc3
# console: camera stays, keys still jog, HUD still updates
ssh nvidia@192.168.223.44 'echo still-here'
```

Bring Ethernet back:

```bash
nmcli connection up "Wired connection 1"
```

Stop:

```bash
# operator PC
cd video && ./stop.sh
cd ../teleoperation-prototype && ./scripts/stop_mlink.sh
# Ctrl-C mlink-op

# Orin (Wi-Fi SSH)
cd /home/nvidia/hassan/video && ./stop.sh
cd /home/nvidia/hassan/teleoperation-prototype && ./scripts/stop_mlink.sh
# Ctrl-C mlink-edge
```

Localhost Zenoh tests (`./scripts/start.sh` + `test_basic.sh` etc.) are
unchanged: both containers on one host, no mlink. They do not replace
the two-host cable-pull.

Topology: [`stage5_overview.md`](stage5_overview.md).

## Layout

```text
mlink-transport/
  README.md                  # stage 0–5 plan (default doc)
  proto/                 # header, config, dedupe, path table, scheduler, session
  daemon.py              # shared op/edge run loop (tick / poll / app face)
  ping.py                # mlink-ping
  op/                    # python3 -m op
  edge/                  # python3 -m edge
  tests/                 # unit + localhost UDP
  config/loopback.yaml       # op side (41001/41002 → 42001/42002)
  config/loopback-edge.yaml  # edge side (42001/42002 → 41001/41002)
  config/lab-op.yaml         # operator (wlo1 + USB-eth); Stage 5 send_media
  config/lab-edge.yaml       # Orin (wlP1p1s0 + eno1); Stage 5 listen_media
  config/lab-op-remote-laptop.yaml   # F18 Tailscale op; allow_tailscale
  config/lab-edge-remote-laptop.yaml # F18 Tailscale edge; control-only
  scripts/start_daemon.sh    # op|edge [--remote-laptop]; no --reflect/--control
  docs/usage.md              # this file — tests, loopback, cable-pull, Stage 5, F18
  docs/stage1_sequence.md
  docs/stage2_overview.md    # processes, ports, ping path
  docs/stage3_overview.md    # two machines, SO_BINDTODEVICE, cable pull
  docs/stage5_overview.md    # apps on 127.0.0.1, control + media
  docs/latency_comparison.md
```

## Header (v1, 32 bytes, little-endian)

```text
offset  size  field
0       4     magic = b'MLNK'
4       1     version = 1
5       1     flags     bit0=heartbeat  bit1=probe  bit2=echo
6       1     traffic_class  0=control  1=media
7       1     path_id   (index into local path table, 0–255)
8       4     session_id     u32
12      4     seq            u32  (shared by all copies of this datagram)
16      8     timestamp_us   u64  (sender monotonic microseconds)
24      2     payload_len    u16
26      2     reserved       u16 = 0
28      4     pad            = 0
32–end        payload (payload_len bytes)
```

- MTU budget: 1500 − 20 (IP) − 8 (UDP) − 32 = **1440** payload. Do not
  fragment. Larger payloads are rejected.
- Dedup key: `(session_id, seq)` — not `path_id`.
- Data `seq` increments only for app payloads. Heartbeats/probes use a
  per-path counter in `seq`, are not delivered to the app, and are not
  stored in the data dedup window.
- `bit2=echo`: reply to a heartbeat/probe, carrying the original
  timestamp so the sender can measure RTT. Echoes are not re-echoed.

## Config schema

```yaml
session_id: 1
allow_tailscale: false         # default. true is a session opt-in for 100.x / tailscale0
listen_app: "127.0.0.1:5501"   # from local apps (control)
send_app:   "127.0.0.1:5502"   # to local apps (control)
listen_media: "127.0.0.1:5004" # optional; from local apps (media)
send_media:   "127.0.0.1:5004" # optional; to local apps (media)
paths:
  - name: eth
    ifname: enx00e04c681cc3    # optional; SO_BINDTODEVICE when set
    bind_ip: 192.168.10.1
    bind_port: 46000           # optional; defaults to peer port
    peer: 192.168.10.2:46000
  - name: wifi
    ifname: wlo1
    bind_ip: 192.168.222.107
    peer: 192.168.223.251:46000
```

Adding a third path is another YAML entry (no protocol change).
`tailscale0` and `100.x` addresses are rejected unless the session
sets `allow_tailscale: true` (F18 Stage 3 remote-laptop pair only).
Orin `lab-op.yaml` / `lab-edge.yaml` omit the flag and still reject
those. Do not put Tailscale IPs in the Orin files.

Defaults (overridable in YAML): heartbeat 100 ms, down after 300 ms
silence, probe at 1 Hz while down, exclude a path from **data** when
inbound heartbeat loss > 20% over the last 20 heartbeats.

Stage 2 loopback uses `127.0.0.1` and two port-pairs, no `ifname`. App
ports on op and edge must not collide. Stage 3 lab YAML sets `ifname`
and real bind/peer IPs (`config/lab-op.yaml`, `config/lab-edge.yaml`).
F18 remote-laptop YAML is `config/lab-op-remote-laptop.yaml` /
`config/lab-edge-remote-laptop.yaml` (`allow_tailscale: true`, one
`tailscale0` path, control-only). Stage 4 starts those with
`./scripts/start_daemon.sh {op,edge} --remote-laptop` (no `--reflect`,
no `--control`). Stage 5 adds optional `listen_media` / `send_media`;
omitted means control-only (loopback / `mlink-ping` / remote-laptop
unchanged).

## Behavior

- Send: increment data seq, copy on every **up** path with loss ≤
  threshold. Down / too-lossy paths are not given data copies.
- Recv: first good copy is delivered immediately. Duplicates and seqs
  older than the dedupe window are dropped. No reorder hold.
- Heartbeats every 100 ms per up path. After 300 ms of silence the path
  is marked down and probed at 1 Hz until it is heard again.
- Control and media are separate queues; flush always drains control
  first so media cannot block it.
- Stage 2: real localhost UDP (`UdpSocketFactory`). `ifname` unset →
  plain bind. Kill a path with UDP text `down <name>` on `--control`
  (closes that socket; does not use iptables).
- Stage 3: when `ifname` is set, `SO_BINDTODEVICE` on that socket.
  May need `CAP_NET_ADMIN` (sometimes documented as `CAP_NET_RAW`).
  Leave `ifname` unset and behavior matches Stage 2.

## Not in Stage 5

Third link (`wwan0` / F9), Tailscale peers, FEC, default-route
changes, embedding mlink in ROS/GStreamer, `rmw_zenoh` through mlink.
