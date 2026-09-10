# mlink-transport

Userspace UDP bonding (LLTP-like). v1 sends a copy of each datagram on
every live path. The receiver keeps the first good copy and drops the
rest. No Linux default-route failover, no Tailscale data path.

Stage 1 is the **protocol library and unit tests** (fake clock, fake
sockets). Stage 2 is **two OS processes on localhost** (`mlink-op` /
`mlink-edge`) plus `mlink-ping`. Real UDP, no real NICs, no
`SO_BINDTODEVICE`.

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

Ports, both processes, and the ping round-trip: `docs/stage2_overview.md`.

## Layout

```text
mlink-transport/
  proto/                 # header, config, dedupe, path table, scheduler, session
  daemon.py              # shared op/edge run loop (tick / poll / app face)
  ping.py                # mlink-ping
  op/                    # python3 -m op
  edge/                  # python3 -m edge
  tests/                 # unit + localhost UDP
  config/loopback.yaml       # op side (41001/41002 → 42001/42002)
  config/loopback-edge.yaml  # edge side (42001/42002 → 41001/41002)
  docs/stage1_sequence.md
  docs/stage2_overview.md    # processes, ports, ping path
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
    ifname: enx00e04c681cc3    # optional; ignored until Stage 3
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
ports on op and edge must not collide.

## Behavior

- Send: increment data seq, copy on every **up** path with loss ≤
  threshold. Down / too-lossy paths are not given data copies.
- Recv: first good copy is delivered immediately. Duplicates and seqs
  older than the dedupe window are dropped. No reorder hold.
- Heartbeats every 100 ms per up path. After 300 ms of silence the path
  is marked down and probed at 1 Hz until it is heard again.
- Control and media are separate queues; flush always drains control
  first so media cannot block it.
- Stage 2: real localhost UDP (`UdpSocketFactory`). `ifname` is ignored.
  Kill a path with UDP text `down <name>` on `--control` (closes that
  socket; does not use iptables).
- `SO_BINDTODEVICE` / real NICs are Stage 3. Binding a real `ifname` may
  need `CAP_NET_ADMIN` (sometimes documented as `CAP_NET_RAW`).

## Not in Stage 2

`SO_BINDTODEVICE`, Orin deploy, Tailscale peers, FEC, ROS, Compose,
default-route changes.
