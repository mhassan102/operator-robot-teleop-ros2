# Multi-link transport (LLTP-like) — implementation plan

Read this file before writing code. **Protocol + unit tests first.**
Do not change ROS 2 teleop Compose, MoveIt, watchdog, or the WebRTC
camera service until Stage 5 is explicitly approved.

This file is the **session handoff**. A new Grok session should not need
the planner chat. Implement **one stage**, then stop.

---

## Session handoff (read this first)

**Git branch:** `mlink-support` (do not merge to `main` unless asked)

**Where we stand:** Stages 0–2 are **done and committed**. Next
implementation session is **Stage 3 only**.

**Exact prompt for the next implementation session:**

```text
Read /home/muhammadhassan/robots/mlink-transport-plan.md from the start.
You are on git branch mlink-support.
Stages 0, 1, and 2 are done and committed. Implement Stage 3 only.
Do not start Stage 4 or 5. Do not change ROS/WebRTC/Compose.
Do not use Tailscale as a data path (SSH only).
Do not change the Linux default route (keep it on Wi-Fi).
Do not git commit (planner session will verify and commit).
Do not re-open locked decisions in this file.

Reuse mlink-op / mlink-edge / mlink-ping and MlinkSession.
Add SO_BINDTODEVICE when ifname is set (UdpSocketFactory).
Stage 3 is operator PC ↔ Orin nvidia-3 on real eth + wifi.
See this file §8 Stage 3 and §10 lab inventory.

Keep `cd mlink-transport && python3 -m pytest` green.
When the two-machine cable-pull demo works, update Stage 3 STATUS
to done and leave commit: remaining. Stop and show the commands.
```

**Planner session (this architecture conversation):** after an
implementation session finishes a stage, come back here to:

1. Verify the stage against this file
2. `git commit` on `mlink-support`
3. Set that stage `commit: done`
4. Paste the next-session prompt (above, with N / N+1 updated)

**Rules for every implementation session:**

- Read this file first, then only the code under `mlink-transport/`
  (once it exists). Do not require the planner chat.
- Implement **exactly one** stage. Stop even if the next stage looks small.
- Do not re-litigate locked decisions.
- Do not touch `teleoperation-prototype/` until Stage 5 is approved.
- Do not `git commit` or `git push` unless the user in that session
  explicitly asks. The planner session owns commits.
- After the stage works: set that stage `STATUS: done`, keep
  `commit: remaining`, list files changed, list test commands, stop.
- If a previous stage’s code is missing or tests fail, **stop** and
  report that. Do not silently redo earlier stages.

---

## Status board

Update these two keys when a stage finishes. Values are only
`remaining` or `done`.

| Stage | What | STATUS | commit |
| ----- | ---- | ------ | ------ |
| 0 | Design (this file) | done | done |
| 1 | Protocol library + unit tests (fake sockets) | done | done |
| 2 | Two-process localhost loopback + `mlink-ping` | done | done |
| 3 | Two machines, real Ethernet + Wi-Fi, cable-pull | remaining | remaining |
| 4 | Third link `wwan0` in config only | remaining | remaining |
| 5 | Zenoh/WebRTC localhost integration | remaining | remaining |

**Next to implement:** Stage 3

---

## Locked decisions (do not re-open)

These were decided in the planner session. Treat them as requirements.

1. **v1 sends a copy of each datagram on every live path.** Copies of
   the same payload share `session` + `seq`. They differ only by
   `path_id`. Receiver delivers the **first good** copy and drops later
   copies of that `(session, seq)`.
2. **No Linux default-route failover.** `mlink` binds sockets to NICs
   (`SO_BINDTODEVICE` / bind IP) and sends to explicit peer IPs. It
   never changes `ip route`. Lab Linux default route stays on **Wi-Fi**
   so SSH survives an Ethernet cable pull.
3. **No `default_link` in mlink config.** There is no primary path.
   Health is measured per path. Unhealthy paths are excluded; they are
   probed in the background and resume when healthy.
4. **Do not pin replies to the path that won.** Each direction
   independently duplicates on all live paths. `path_id` is for stats,
   not return-path affinity.
5. **Do not wait.** No reorder hold, no wait-for-other-path, no wait
   for FEC before deliver. Late packets after the window are dropped.
   WebRTC/RTP (later) has its own jitter buffer; mlink must not add a
   second one. Control must not sit behind a video burst (separate
   queues or priority).
6. **Tailscale is SSH/management only.** Never a bonded path. Do not
   bind `tailscale0`. Do not use `100.x` peer IPs in mlink config.
7. **Stages 0–2 run on the operator PC only** (localhost / fake
   sockets). Orin is not in the data path until Stage 3.
8. **Language for v1: Python 3** with stdlib + PyYAML, tests via
   pytest. Must run on Ubuntu x86_64 and Orin aarch64. No ROS
   dependency in `mlink-transport/`.
9. **FEC is not in Stages 1–3.** Optional XOR / bitrate hook can wait.
   Leave a stub only if it costs nothing; do not implement FEC.
10. **Bandwidth (duplicate-all) is the v1 cost, not extra latency.**
    Userspace hop should stay small. Do not add buffers that wait.

---

## 1. Goal

Implement a **userspace UDP bonding pair** (not Voysys LLTP; same ideas)
so the operator PC and the robot edge (Jetson Orin) can use **several
uplinks at once**:

- send copies (or later stripes) of each datagram on every live link
- receiver keeps the **first good** copy and drops duplicates
- if one link dies, traffic continues on the others **without** waiting
  for a Linux default-route change
- if a link is up but lossy, prefer healthier links (**steer around loss**)
- video and control will ride this path later; **this work is the protocol**

Start with **2 links** (Ethernet + Wi-Fi). Design so a **3rd** link
(USB 5G dongle / `wwan0`) and further links are **config only**.

Failover routing scripts on the Orin host are a **demo** path only.
This protocol is the production-oriented path.

Production teleops (later): robot in another building, multiple WANs
(Ethernet / Wi-Fi / 5G) toward a reachable operator IP or relay.
Tailscale is not that path.

---

## 2. Non-goals (until Stage 5 is approved)

- Do not modify ROS 2 teleop containers, MoveIt, watchdog, or the
  existing WebRTC camera service
- Do not implement real Voysys LLTP
- Do not depend on kernel MPTCP or a hardware bonding router
- No research-grade FEC/codec work — a **simple, testable** subset is
  enough for v1
- Do not use Tailscale as a data path
- Do not implement Stage N+1 in the same session as Stage N

---

## 3. Architecture

Two userspace processes. They sit **under** Zenoh and WebRTC, **beside**
the ROS containers, not inside `ros2_control`.

```text
OPERATOR PC                         ORIN
browser + keyboard                  camera + ROS container
        │                                   │
        ▼                                   ▼
  mlink-op                            mlink-edge
        │         several WANs          │
        │    eth / wifi / 5G dongle     │
        └──────── UDP bonded paths ─────┘

Apps later send to 127.0.0.1:<port> on each side.
They never see three public IPs.
```

Both processes are **symmetric** (each sends and receives). Names are
deployment roles, not one-way pipes.

| Process        | Where            | Role |
| -------------- | ---------------- | ---- |
| `mlink-edge`   | Orin (Stage 3+)  | Bind each local interface, encapsulate, send on all **active** paths, dedupe inbound, emit one stream to localhost |
| `mlink-op`     | operator Ubuntu  | Same protocol. In Stages 1–2 both processes run on this PC |

Payload is opaque bytes plus a small header:

- session id
- sequence number (same on every copy of that datagram)
- path id (which NIC/socket sent this copy)
- timestamp
- traffic class: `control` vs `media`

---

## 4. v1 protocol features

| Capability | v1 requirement |
| ---------- | -------------- |
| Several links at once | Duplicate each datagram on **all configured up paths**. Optional later: stripe large media. |
| Link drop | Remaining paths already carry the packet; no routing-script gap. |
| Video-ready | Datagram API + seq so RTP/WebRTC can be tunneled later. FEC **not** in Stages 1–3. |
| Steer around loss | Per-path stats: RTT or inter-arrival, loss, last-heard. Stop using a path if loss/RTT exceeds thresholds; probe it in the background; resume when healthy. |
| Extensible links | YAML list of `{name, bind_ip or ifname, peer_ip:port}`. Adding `wwan0` is config. |

Also required:

- path heartbeat
- path marked down after timeout
- max queue / window (drop, do not block control behind media)
- control vs media queues

---

## 5. Comparison (intent)

| | Host routing script | This protocol (target) |
| - | ------------------- | ---------------------- |
| What it does | One link at a time; switch when the current one dies | Several links at once; first-good copy / later split load |
| During a drop | Gap while the route changes (hundreds of ms to a few s) | Other link already has the packet |
| Video | WebRTC still on one path | Multi-path now; FEC/bitrate later |
| When links are up but lossy | Stays on the bad default until it is marked down | Steers around loss |

---

## 6. Repo layout

New folder `mlink-transport/` (not inside the robot Compose image until
Stage 5).

```text
mlink-transport/
  README.md
  proto/          # header, encode/decode, dedupe, path table, scheduler
  edge/           # mlink-edge CLI (Stage 2+)
  op/             # mlink-op CLI (Stage 2+)
  tests/          # unit + loopback
  config/         # example link lists
```

Stage 1 may keep library + tests only; CLIs can wait until Stage 2.

---

## 7. Stage 0 — Design (locked)

- **STATUS:** done
- **commit:** done
- **Code:** none. Design lives in this file.

### 7.0.1 Header (v1, 32 bytes, little-endian)

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

Locked in Stage 1 (`mlink-transport/proto/header.py`). Do not change.

- MTU budget: 1500 − 20 (IP) − 8 (UDP) − 32 = 1440 payload. Do not
  fragment. Reject payloads that would exceed this on a path.
- Dedup key: `(session_id, seq)` — **not** path_id.
- Data `seq` increments only for app payloads. Heartbeats/probes use a
  **per-path** counter in `seq`, are not delivered to the app, and are
  not stored in the data dedup window.
- `bit2=echo`: reply to a heartbeat/probe, carrying the original
  timestamp so the sender can measure RTT. Echoes are not re-echoed.

### 7.0.2 Sockets

- One UDP socket per configured path.
- Bind to `bind_ip` (and `SO_BINDTODEVICE` when `ifname` is set).
- `sendto(peer_ip, peer_port)`.
- App face (Stage 2+): `127.0.0.1` UDP in/out. Apps never see WAN IPs.
- v1: explicit peer IPs, no discovery.
- Prefer not to need `CAP_NET_RAW`. Document if `SO_BINDTODEVICE`
  requires `CAP_NET_RAW` / `CAP_NET_ADMIN`.
- Never bind `tailscale0`. Never use `100.x` peers.

### 7.0.3 Duplicate / dedupe / deliver

```text
send(payload, class):
  seq += 1
  for path in paths where path.up and path.loss <= threshold:
    send copy(header(seq, path_id), payload) on that socket
  # DOWN paths: do not send data; probe separately

recv(copy):
  if heartbeat/probe: update path stats; do not deliver to app
  elif (session, seq) already delivered: drop
  elif seq too old for window: drop
  else: mark delivered, emit payload to app immediately
```

Example (correct): seq=1 first copy on eth → deliver; wifi copy of
seq=1 → drop. seq=2 first copy on wifi → deliver; eth copy of seq=2 →
drop. That is not path pinning.

Do **not** hold seq=2 until seq=1 arrives.

### 7.0.4 Loss, RTT, up/down

- **Heartbeat** every 100 ms per path (configurable).
- **Down:** no data or heartbeat received on that path for 300 ms
  (configurable). Mark down, stop sending data copies there.
- **Probe:** while down, send occasional probe/heartbeat (e.g. 1 Hz).
  If answers resume, mark up.
- **Loss:** sliding window (e.g. last 1 s or last N copies expected on
  that path). `loss = 1 - (copies_received / copies_sent_or_expected)`.
  Default exclude threshold: 20% (configurable).
- **RTT:** heartbeat timestamp echoed, or send timestamp vs recv time
  for probes. Store smoothed RTT per path.
- Scheduler v1: send on all **up** paths with loss ≤ threshold.
  “Fastest link” is an *outcome* (first copy wins), not a config key.

### 7.0.5 Adding a 3rd link

Add a YAML entry. No protocol change. Stage 4 is config + a dongle,
not a new codepath if Stage 1 tests already cover “path C in config.”

Example schema (names can be refined in Stage 1, keep this shape):

```yaml
session_id: 1
listen_app: "127.0.0.1:5501"   # from local apps
send_app:   "127.0.0.1:5502"   # to local apps
paths:
  - name: eth
    ifname: enx00e04c681cc3    # operator; Orin uses eno1
    bind_ip: 192.168.10.1
    peer: 192.168.10.2:46000
  - name: wifi
    ifname: wlo1
    bind_ip: 192.168.222.107
    peer: 192.168.223.251:46000
  # Stage 4:
  # - name: lte
  #   ifname: wwan0
  #   bind_ip: ...
  #   peer: ...
```

Loopback (Stage 2) uses `127.0.0.1` and two port pairs, no `ifname`.

### 7.0.6 Delay budget

Encapsulation is an extra userspace hop. On WAN teleops that should be
noise vs Wi-Fi/WAN/5G **if** mlink never waits. Do not add jitter
buffers. Keep header 32 bytes so packets do not fragment.

---

## 8. Stages 1–5 (contracts)

Stop after each stage unless told to continue.

### Stage 1 — Protocol library + unit tests (no real NICs)

- **STATUS:** done
- **commit:** done
- **Depends on:** Stage 0 (this file)
- **Where:** operator PC only. No Orin, no Wi-Fi, no Tailscale, no ROS.
- **Verify:** `cd mlink-transport && python3 -m pytest` — 27 passed.

**Build:**

- Encode/decode header
- Dedup by `(session, seq)`
- Path table: up/down, loss, RTT, last-heard
- Scheduler: send on all up paths; exclude path if loss > X
- Fake clock + fake sockets (injectable)

**Tests (must exist and pass):**

- packet arrives twice → one delivered
- path B dead → still delivered via A
- path A 30% loss, B clean → send/prefer B (no data copies on A if
  over threshold; B still delivers)
- add path C in config → traffic on C
- reorder / late packet dropped after window
- heartbeat timeout marks path down; probe brings it back

**Do not build in Stage 1:** real CLI daemons, real NICs, FEC, ROS,
Compose, git commit.

**Done means:** `pytest` (or equivalent) all green; README in
`mlink-transport/` how to run tests; header + config schema documented
in README or this file (keep them matching).

**Code the next session should read:** this file §8 Stage 2;
`mlink-transport/README.md`; `mlink-transport/proto/` (especially
`session.py`, `sockets.py` `SocketFactory`, `config.py`);
`mlink-transport/config/loopback.yaml`;
`mlink-transport/docs/stage1_sequence.md`.

---

### Stage 2 — Two-process local loopback

- **STATUS:** done
- **commit:** done
- **Depends on:** Stage 1 tests green
- **Verify:** `cd mlink-transport && python3 -m pytest` — 34 passed
  (includes 1000-datagram kill-eth subprocess). Loopback demo
  commands in `mlink-transport/README.md`.
- **Where:** operator PC only. Two OS processes, two UDP port pairs
  pretending to be eth and wifi (`127.0.0.1`). No Orin, no real NICs.

**Reuse (do not rewrite):**

- `MlinkSession` for send-copies / first-good / heartbeats / probes
- `load_config` / `MlinkConfig` (add a second YAML for the peer side;
  `loopback.yaml` is one side only)
- `SocketFactory` protocol — add a **real UDP** implementation
- `SystemClock` + a run loop that calls `tick()` / `poll()` / `flush()`

**Build:**

- Real localhost UDP sockets (`bind` + `sendto` / `recvfrom`, non-blocking
  or short timeout). **No** `SO_BINDTODEVICE`. `ifname` in YAML is ignored.
- `mlink-op` and `mlink-edge` CLIs (one module + two configs is fine).
  Each owns one `MlinkSession`. App face is `listen_app` / `send_app`
  on `127.0.0.1` (already in YAML).
- Complementary configs: op binds `41001`/`41002` and peers `42001`/`42002`;
  edge binds `42001`/`42002` and peers `41001`/`41002`. App ports must
  not collide.
- `mlink-ping`: send **1000** datagrams into one daemon’s `listen_app`;
  the far side echoes (tiny reflector on `send_app`, or `mlink-ping --reflect`).
  Mid-run, **kill one local path** (close/disable that port-pair in
  mlink, not iptables). Report delivered count, loss, and max inter-arrival
  gap. Stream must continue on the other path.
- README: exact commands to run op, edge, ping, and the kill-path demo.
- Keep Stage 1 `pytest` green. Add Stage 2 tests if they stay off real
  NICs (localhost UDP in-process or subprocess is OK).

**Do not build:** `SO_BINDTODEVICE`, Orin deploy, Tailscale peers, FEC,
ROS, default-route changes, Stage 3 hardware.

**Done means:** documented loopback demo; killing one local path does
not lose the 1000-datagram stream (small gap OK); unit tests still green.

**Code the next session should read:** this file §8 Stage 3 and §10;
`mlink-transport/README.md`; `daemon.py`; `ping.py`; `proto/sockets.py`
(`UdpSocketFactory`); `config/loopback.yaml` + `loopback-edge.yaml`.

---

### Stage 3 — Two machines, Ethernet + Wi-Fi

- **STATUS:** remaining
- **commit:** remaining
- **Depends on:** Stage 2 loopback demo (committed)
- **Where:** operator PC ↔ Orin `nvidia-3`

**Hardware (this stage — recable now):**

```text
OPERATOR (pure-dev-muhammadhassan)          ORIN (nvidia-3)
wlo1  192.168.222.107  -- Guest Wi-Fi --    wlP1p1s0  192.168.223.251
USB-eth enx00e04c681cc3 192.168.10.1 --cable-- eno1  192.168.10.2
tailscale0 = SSH only (100.95.150.54 / 100.101.94.5)
```

- Plug USB-Ethernet dongle on this PC into Orin `eno1`.
- Static `/24` on that cable (`192.168.10.0/24`). **Not** the Guest
  subnet `192.168.222.0/23`.
- Linux default route **stays on Wi-Fi**. Do not make `192.168.10.0/24`
  the default.
- Keep Tailscale up for `ssh nvidia@nvidia-3`. Also keep
  `ssh nvidia@192.168.223.251` as a second SSH path.
- Bind real ifnames. Proof is `path_id` logs + tcpdump on both NICs +
  cable pull, **not** `ip route`.

**Reuse:** Stage 2 `mlink-op` / `mlink-edge` / `mlink-ping` / `MlinkSession`.

**Build:**

- `UdpSocketFactory`: when `ifname` is set, `SO_BINDTODEVICE` on that
  socket. Document if `CAP_NET_ADMIN` is required. Leave `ifname` unset
  → same as Stage 2 (plain bind).
- Operator YAML (`wlo1` + `enx00e04c681cc3`) and Orin YAML (`wlP1p1s0` +
  `eno1`) with the IPs above. Not `100.x`, not `tailscale0`.
- README: assign Ethernet IPs without changing default route; copy
  `mlink-transport/` to Orin; run op here and edge there; `mlink-ping`
  + cable pull.
- Keep pytest green. Localhost tests must still pass without NICs.

**Test:** pull Ethernet cable; ping-tool and a dummy ~1 Mbps stream
keep going on Wi-Fi with a small gap. Log which path each packet used.
SSH over Wi-Fi/Tailscale must survive the pull.

**Do not:** bond over Tailscale; put Ethernet on the Guest LAN; change
default route; start ROS/WebRTC.

**Code the next session should read:** Stage 2 CLIs + this contract + §10.

---

### Stage 4 — Third link

- **STATUS:** remaining
- **commit:** remaining
- **Depends on:** Stage 3 cable-pull pass
- **Where:** USB 5G dongle on the **Orin**, interface `wwan0` (name
  may vary). Operator does not need a dongle.

Add the path **in config only**. Repeat cable-pull / path-down tests.

Do **not** start ROS/WebRTC integration in this stage.

---

### Stage 5 — Integration (later, separate approval)

- **STATUS:** remaining
- **commit:** remaining
- **Depends on:** Stages 1–4 pass **and** explicit user approval

Localhost ports in front of Zenoh and WebRTC. Robot container unchanged
except “send to mlink localhost.”

This stage does **not** require 5G. Do not start it in a Stage 4
session.

---

## 9. Acceptance

**Stages 1–2 first:**

- Unit tests all green (`pytest` / equivalent)
- README: how to run unit tests and the loopback demo
- Documented header and config schema
- No ROS, no camera, no Tailscale required for unit tests

**Stage 3:** cable pull on real eth+wifi; stream continues; SSH stays up.

**Stage 4:** third path from config; no Compose changes.

**Stage 5:** only after written approval.

---

## 10. Lab inventory (checked in planner session)

Operator PC `pure-dev-muhammadhassan`:

| If | Addr | Role |
| -- | ---- | ---- |
| `wlo1` | `192.168.222.107/23` Guest | mlink wifi path (Stage 3); Linux default route; SSH underlay |
| `enx00e04c681cc3` | down, no carrier | USB Ethernet; Stage 3 cable to Orin `eno1` |
| `tailscale0` | `100.95.150.54` | SSH only |

Orin `nvidia-3`:

| If | Addr | Role |
| -- | ---- | ---- |
| `wlP1p1s0` | `192.168.223.251/23` Guest | mlink wifi path |
| `eno1` | down, no carrier | Stage 3 Ethernet |
| `tailscale0` | `100.101.94.5` | SSH only (`ssh nvidia@nvidia-3`) |
| `wwan0` | absent | Stage 4 dongle (not installed) |
| `usb0`/`usb1` | gadget ports | **not** a modem |

Guest Wi-Fi already pings without Tailscale:
`192.168.222.107` → `192.168.223.251`. Direct SSH:
`ssh nvidia@192.168.223.251`.

Stages 1–2 needed none of this hardware. Stage 3 recables Ethernet.

---

## 11. Constraints

- Lab-safe: prefer UDP bind + `SO_BINDTODEVICE`. Document if
  `CAP_NET_RAW` / `CAP_NET_ADMIN` is required.
- v1 uses explicit peer IPs (no discovery).
- Control packets must not wait behind a large video burst.
- `mlink` must not install iptables, must not take port 22, must not
  take Tailscale UDP 41641, must not change the default route.
- If `mlink` crashes, SSH via Guest Wi-Fi and Tailscale must still work.

---

## 12. Context (read-only)

Read `teleoperation-prototype/` and the teleop implementation plan for
**context only**. Do not change that Compose stack until Stage 5.

---

## 13. Code map (update as stages land)

| Path | Owner stage | Notes |
| ---- | ----------- | ----- |
| `mlink-transport-plan.md` | 0 | this file |
| `mlink-transport/README.md` | 1+2 | header, tests, Stage 2 loopback commands |
| `mlink-transport/proto/header.py` | 1 | 32-byte encode/decode, `Packet` |
| `mlink-transport/proto/config.py` | 1 | YAML load; rejects `tailscale0` / `100.x` |
| `mlink-transport/proto/clock.py` | 1 | `Clock` / `FakeClock` / `SystemClock` |
| `mlink-transport/proto/sockets.py` | 1+2 | Fake sockets + `UdpSocketFactory` (no `SO_BINDTODEVICE`) |
| `mlink-transport/proto/path.py` | 1 | up/down, loss, RTT, last-heard |
| `mlink-transport/proto/dedupe.py` | 1 | first-good `(session, seq)`; late after window |
| `mlink-transport/proto/scheduler.py` | 1 | all up paths with loss ≤ threshold |
| `mlink-transport/proto/session.py` | 1 | `MlinkSession`: send copies, poll, tick HB/probe |
| `mlink-transport/tests/` | 1 | pytest, fake clock + sockets |
| `mlink-transport/config/` | 1+2 | `example.yaml`, `loopback.yaml` (op), `loopback-edge.yaml` |
| `mlink-transport/docs/stage1_sequence.md` | 1 | sequence diagrams for the library |
| `mlink-transport/daemon.py` | 2 | op/edge run loop, app face, `down <path>` control |
| `mlink-transport/ping.py` | 2 | `mlink-ping` (1000 datagrams, kill-path, `--reflect`) |
| `mlink-transport/op/` | 2 | `python3 -m op` |
| `mlink-transport/edge/` | 2 | `python3 -m edge` |
| `mlink-transport/tests/test_stage2.py` | 2 | localhost UDP + subprocess loopback demo |

---

## 14. Planner checklist (after an implementation session)

- [ ] Stage N tests/demo match the contract above
- [ ] Locked decisions still held (no default route, no Tailscale path,
      no reorder buffer)
- [ ] `STATUS: done` for that stage
- [ ] Commit on `mlink-support` with a stage-scoped message
- [ ] Set `commit: done`
- [ ] Refresh “Exact prompt for the next implementation session”
- [ ] Do not start Stage N+1 in the planner session unless asked
