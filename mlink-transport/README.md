# mlink-transport

Userspace UDP bonding (LLTP-like). v1 sends a copy of each datagram on
every live path. The receiver keeps the first good copy and drops the
rest. No Linux default-route failover, no Tailscale data path.

Stage 1 is the **protocol library and unit tests** (fake clock, fake
sockets). No real NICs, no CLI daemons, no ROS.

## Run tests

From this directory (Python 3.10+, Ubuntu x86_64 or Orin aarch64).
Needs `pytest` and `PyYAML` (`pip install -r requirements.txt`).

```bash
cd mlink-transport
python3 -m pytest
```

## Layout

```text
mlink-transport/
  proto/          # header, config, dedupe, path table, scheduler, session
  tests/          # unit tests (fake sockets)
  config/         # example link lists
  edge/           # mlink-edge CLI (Stage 2+)
  op/             # mlink-op CLI (Stage 2+)
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
listen_app: "127.0.0.1:5501"   # from local apps (Stage 2+)
send_app:   "127.0.0.1:5502"   # to local apps (Stage 2+)
paths:
  - name: eth
    ifname: enx00e04c681cc3    # optional; ignored until real sockets
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

## Stage 1 behavior

- Send: increment data seq, copy on every **up** path with loss ≤
  threshold. Down / too-lossy paths are not given data copies.
- Recv: first good copy is delivered immediately. Duplicates and seqs
  older than the dedupe window are dropped. No reorder hold.
- Heartbeats every 100 ms per up path. After 300 ms of silence the path
  is marked down and probed at 1 Hz until it is heard again.
- Control and media are separate queues; flush always drains control
  first so media cannot block it.
- `SO_BINDTODEVICE` / real UDP is Stage 2–3. Stage 1 injects
  `FakeClock` and `FakeNetwork`. Binding a real `ifname` may need
  `CAP_NET_ADMIN` (sometimes documented as `CAP_NET_RAW`); that is not
  required for these tests.

## Not in Stage 1

CLI daemons (`mlink-edge` / `mlink-op`), real NICs, FEC, ROS, Compose.
