# mlink-transport — how to run

Userspace UDP bonding (LLTP-like). v1 sends a copy of each datagram on
every live path. The receiver keeps the first good copy and drops the
rest. No Linux default-route failover, no Tailscale data path.

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
  config/lab-op.yaml         # Stage 3 operator (wlo1 + USB-eth)
  config/lab-edge.yaml       # Stage 3 Orin (wlP1p1s0 + eno1)
  docs/usage.md              # this file — tests, loopback, cable-pull
  docs/stage1_sequence.md
  docs/stage2_overview.md    # processes, ports, ping path
  docs/stage3_overview.md    # two machines, SO_BINDTODEVICE, cable pull
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
listen_app: "127.0.0.1:5501"   # from local apps
send_app:   "127.0.0.1:5502"   # to local apps
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
`tailscale0` and `100.x` addresses are rejected.

Defaults (overridable in YAML): heartbeat 100 ms, down after 300 ms
silence, probe at 1 Hz while down, exclude a path from **data** when
inbound heartbeat loss > 20% over the last 20 heartbeats.

Stage 2 loopback uses `127.0.0.1` and two port-pairs, no `ifname`. App
ports on op and edge must not collide. Stage 3 lab YAML sets `ifname`
and real bind/peer IPs (`config/lab-op.yaml`, `config/lab-edge.yaml`).

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

## Not in Stage 3

Third link (`wwan0`), Tailscale peers, FEC, ROS, Compose,
default-route changes.
