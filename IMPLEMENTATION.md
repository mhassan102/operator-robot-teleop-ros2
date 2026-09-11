# Remote teleoperation — product implementation plan

Read this file first in a new session. Feature-level contracts live in
the subdirectories. This file is the **product roadmap**: what the POC
already is, what we will add to make it a product, and enough detail
that a later Grok session can implement **one feature** without the
planner chat.

This session is planning and docs only. **Do not write feature code
from this file until a later session is given a resume prompt below.**

---

## How to use this file

**Planner session (this conversation and later check-ins):** after an
implementation session finishes a feature (or a substage):

1. Verify it against the contract in this file (and the feature dir)
2. Set that row `STATUS: done` (or `blocked` / `on hold`)
3. Commit only if the user in that session asked
4. Paste the next-session prompt

**Implementation session:**

- Read this file from the start, then only the files listed under that
  feature.
- Implement **exactly one** feature or substage. Stop even if the next
  one looks small.
- Do not re-open locked decisions.
- Do not start a `blocked` or `on hold` or `deferred` row.
- Do not `git commit` unless the user in that session explicitly asks.

---

## 1. Product intent

Turn this lab POC into a **remote teleoperation product** we can put
in front of an operator in a browser, with a bonded WAN, a software
safety gateway, and our own motion stack.

We are **not** cloning Adamo’s business model. Adamo sells an agent +
cloud UI + their transport; the customer owns the arm and the planner.
We own the **operator backend, the motion stack, and (for now) the
lab arm**. The operator is a browser talking to *our* backend, not a
ROS desktop and not Adamo’s cloud.

**Product v1 (this plan’s end state, still lab-owned arm):**

- One web console: video + keyboard (later gamepad) + telemetry +
  named poses + software E-stop
- Operator **backend** (evolved ROS 2 operator container) on the
  operator PC
- Robot: safety gateway + MoveIt Servo + Gazebo (real arm later)
- mlink bonding Ethernet + Wi-Fi (5G when the dongle exists)
- Orin HW H.264 into that same console
- No fleet, no hired-operator marketplace, no SOC2, no IEC robot cert

**Out of v1:** fleet / hired operators, session recording (teammate /
LeRobot), VR, certified safety PLC, Adamo-style cloud relay as the
primary path.

---

## 2. Repo map

High-level docs stay at the **repo root**. Feature docs stay in the
feature directory, including work that is on hold.

```text
robots/
  README.md                 # how to navigate
  IMPLEMENTATION.md         # this file
  teleoperation-prototype/  # ROS 2 operator + robot (Compose, Gazebo, Servo)
    docs/                   # architecture, POC plan, GUI steps, tests
  mlink-transport/          # userspace UDP bonding
    docs/plan.md            # stage 0–5 contracts
  safety/                   # 25-function coverage; Zone 1 plan (on hold)
  video/                    # lab WebRTC notes; encoder tree not in git yet
```

New code for the console lands under `teleoperation-prototype/`
(operator service + `web/`). New video packaging lands under `video/`.
mlink stays in `mlink-transport/`. Safety stays in the existing
`teleop_demo` nodes (no second safety stack).

---

## 3. Status board

Update `STATUS` when a feature finishes or is paused.
Values: `done`, `remaining`, `partial`, `blocked`, `on hold`, `deferred`.

| ID | Feature | STATUS | Notes |
| -- | ------- | ------ | ----- |
| F1 | Core ROS teleop POC | done | Containers, stamped commands, Gazebo, Servo, named poses, keyboard |
| F2 | Local Zenoh | done | Two containers, `rmw_zenoh_cpp`, robot runs `rmw_zenohd` |
| F3 | mlink stages 0–3 | done | Protocol, localhost loopback, eth+wifi cable-pull |
| F4 | Safety v0 (jog watchdog) | done | 500 ms gateway on `/cmd_vel_safe` only; named pose bypasses |
| F5 | Operator backend + web console | **remaining (next)** | Localhost first; keyboard + telemetry; camera panel may stub |
| F6 | Video into this repo + console embed | remaining | Lab preview exists on Orin, not in git |
| F7 | Orin HW encode verify / efficiency | partial | Lab gst already uses `nvv4l2h264enc`; not proven in-product |
| F8 | mlink Stage 5 (apps on 127.0.0.1) | blocked | Blocked on F5 shape; Stage 4 not required |
| F9 | mlink Stage 4 (5G / `wwan0`) | blocked | USB dongle not on the Orin |
| F10 | Safety-A local harden | **on hold** | Required before WAN / real arm; do not start until unblocked |
| F11 | Safety-B WAN | remaining | After F10; `heartbeat_only`, E-stop, Reset |
| F12 | WAN / Jetson ROS split | remaining | Today both containers are on one host |
| F13 | Gamepad in the console | remaining | After F5; same backend API |
| F14 | Video-freshness stop | remaining | After F6; stale video must not leave the arm live |
| F15 | Bitrate adaptation | remaining | After media rides mlink or a measured WAN |
| F16 | Console TLS / auth | remaining | Before anyone who is not us opens the UI |
| F17 | Multi-camera | remaining | After one camera is in the console |
| F18 | Real hardware arm | remaining | After F10 + F12; driver behind the same gateway |
| F19 | Benchmarks | remaining | Local vs WAN; command, watchdog, video |
| F20 | mlink payload encryption | remaining | v1 is plaintext UDP; later |
| F21 | CGNAT relay (5G reachability) | remaining | Ops/config when F9 exists; not a new protocol |
| — | Fleet / hired operators | deferred | Explicitly out of v1 |
| — | Session recording | deferred | Teammate / LeRobot |
| — | VR | deferred | |
| — | SOC2 / IEC / ISO 10218 cert | deferred | |

**Next to implement:** F5 (operator backend + web console), localhost.
Do not start F8 until F5’s shape is running. Do not start F10 unless
the user unblocks Safety-A. Do not start fleet.

---

## 4. Gap vs Adamo (software only)

| Topic | Adamo | This repo now | v1 target |
| ----- | ----- | ------------- | --------- |
| What you ship | Agent + cloud UI + their transport | Compose + mlink + extra Chrome tab | Backend + web console + mlink + our motion |
| Who owns the arm | Customer | Lab | Lab, then integrator’s arm behind the same gateway |
| Who owns motion | Customer (MoveIt / vendor) | **We do** (Servo + named poses + Gazebo) | Keep — differentiator |
| Who owns the operator PC | Browser on their site | Ubuntu + ROS operator container + TTY | Browser → **our** backend |
| WAN | Their protocol + bonding + relay | Zenoh on Docker bridge; mlink eth+wifi; Tailscale = SSH | mlink under control + media; Tailscale still SSH only |
| Video protocol | Custom, not WebRTC | WebRTC Orin → Chrome (lab, Tailscale ICE) | WebRTC to the **console**; WAN via mlink localhost, not Tailscale |
| Operator input | Dashboard + gamepad + VR | ROS `keyboard_teleop` | Web keyboard first, gamepad next, VR out |
| Multi-link | LTE+5G+Wi-Fi, steer around loss | Duplicate UDP, first-good; 5G not on box | Same protocol; 5G is YAML when dongle exists |
| Safety | Heartbeat stop, video-freshness; SOC2 claims | 500 ms watchdog in front of `ros2_control` | Safety-A/B + video-freshness; still not a certified PLC |
| Video transport | Custom + multi-path + bitrate adapt | WebRTC on one underlay (Tailscale) | Tunnel through mlink; bitrate adapt later |
| ROS 2 both sides | Agent on robot; operator is their UI | **Yes** — operator + robot | Keep ROS on both; browser does not speak ROS |
| Video encode | Jetson HW H.264 on the video path | Lab gst uses `nvv4l2h264enc`; not in this git tree | Import, confirm, keep HW encode |
| Operator UI | One web console | Terminal + separate Chrome tab | One web console |
| Session recording | Synced video + telemetry + commands | Not here | Deferred (teammate / LeRobot) |
| Reach the box | Their relay / multi-path | Tailscale SSH; mlink binds real NICs | mlink; relay only if 5G is CGNAT |
| Fleet | Yes | No | Deferred |

---

## 5. Architecture as built

```text
Host keyboard (TTY) + Gazebo GUI          Chrome (separate tab)
        |                                        |
        v                                        v
+--- operator container ----+            MediaMTX on Orin :8889
| keyboard_teleop           |            (Tailscale ICE — lab only)
|   /teleop/command         |
|   /teleop/heartbeat       |
+-------------+-------------+
              | rmw_zenoh_cpp  (robot rmw_zenohd :7447)
              | Docker bridge ros2_teleop_poc_net
              v
+--- robot container -------------------------+
| robot_receiver + SafetyController           |
|   /cmd_vel_safe  /gripper_safe /teleop/state|
| servo_bridge → MoveIt Servo → Gazebo arm    |
| named_pose  /teleop/go_named_pose  (bypass) |
+---------------------------------------------+

mlink-op  <== eth + wifi copies ==>  mlink-edge     # ping only today
   (this PC)                         (Orin nvidia-3)
```

ROS interfaces (do not invent a parallel motion wire):

| Topic / service | Direction | Type |
| --- | --- | --- |
| `/teleop/command` | operator → robot | `teleop_demo_msgs/TeleopCommand` |
| `/teleop/heartbeat` | operator → robot | `teleop_demo_msgs/TeleopHeartbeat` |
| `/teleop/ack` | robot → operator | `teleop_demo_msgs/TeleopAck` |
| `/teleop/state` | robot → operator | `teleop_demo_msgs/TeleopState` |
| `/cmd_vel_safe` | safety → servo_bridge | `geometry_msgs/Twist` |
| `/gripper_safe` | safety → servo_bridge | `std_msgs/Float64` |
| `/teleop/tool_pose` | servo_bridge → all | `geometry_msgs/PoseStamped` |
| `/teleop/go_named_pose` | operator → robot | `GoNamedPose` |

Existing tests (keep green): `test_basic.sh`, `test_delivery.sh`,
`test_watchdog.sh`, `test_sim.sh`, `test_named_pose.sh`,
`cd mlink-transport && python3 -m pytest`.

---

## 6. Target architecture (product v1)

```text
OPERATOR PC                                      ORIN
┌─────────────────────────────────────┐          ┌─────────────────────────────┐
│ Chrome                              │          │ camera → nvv4l2h264enc      │
│   keyboard / gamepad / e-stop       │          │ RTP 127.0.0.1               │
│   video + telemetry + named poses   │          │ robot ROS: safety, Servo,   │
└──────────────┬──────────────────────┘          │   Gazebo or real driver     │
               │ HTTP/WS + WebRTC                │ mlink-edge app face         │
               │ localhost only                  │   127.0.0.1 control + media │
               v                                 └──────────────▲──────────────┘
        operator backend (Compose)                              │
        ROS 2 + Zenoh-or-UDP-bridge                             │
        mlink-op app face 127.0.0.1                             │
               │                                                │
               └──────── mlink copies on eth / wifi / 5G ───────┘
                         Tailscale is SSH only
```

Browser **never** speaks ROS, never sees WAN IPs, never talks to three
NICs. The backend is the only operator-side app. mlink is a datagram
pipe. Safety stays on the **robot**, in front of `ros2_control`.

---

## 7. Locked decisions (do not re-open)

1. **Tailscale is SSH / management only.** Never a bonded path, never
   ICE for the product video path. Do not bind `tailscale0` in mlink.
   The current MediaMTX Tailscale ICE is a lab shortcut to be removed
   in F6/F8.
2. **mlink v1 duplicates each datagram on every live path.** First
   good `(session, seq)` wins. No Linux default-route failover. No
   reorder hold. Control queue is flushed before media.
3. **Do not change `ip route`.** Lab default stays on Wi-Fi so SSH
   survives an Ethernet pull.
4. **Safety gateway is the only motion path into Servo.** New UI,
   gamepad, and named poses must not grow a second writer around it
   (Safety-A closes the named-pose hole; that work is on hold but the
   rule stands).
5. **Browser talks to our backend, not to ROS.** No `rosbridge` as the
   product API. Same `TeleopCommand` / heartbeat / named-pose service
   on the ROS side.
6. **Evolve the existing operator Compose service.** Do not add a
   third ROS container for the UI. Robot container stays the motion
   and safety process.
7. **F5 is localhost first.** `127.0.0.1` bind on the operator PC.
   No auth in F5. TLS/auth is F16.
8. **mlink Stage 5 waits for F5.** Apps send opaque UDP to
   `127.0.0.1`. Do not wire today’s TTY operator into mlink.
9. **Control over mlink is a localhost UDP bridge of the existing
   teleop messages, not TCP-Zenoh stuffed into mlink.** Zenoh remains
   the **local** ROS transport (inside a host / Docker bridge). Across
   the WAN, the backend and the robot-side shim exchange opaque
   datagrams on mlink’s `control` class. Media is the `media` class.
   Do not put `rmw_zenoh` TCP 7447 through mlink.
10. **WebRTC terminates next to the operator backend** (localhost).
    The browser does not ICE across eth/wifi/5G. Encoder output is
    RTP/datagrams into mlink on the Orin.
11. **Keep ROS 2 on both sides.** That is a differentiator vs Adamo.
    The operator ROS graph is the backend, not a human desktop.
12. **Keep our motion stack.** MoveIt Servo + named poses + Gazebo
    now; real arm later behind the same `/cmd_vel_safe` path.
13. **Fleet, hired operators, VR, SOC2, session recording, IEC
    cert** are deferred. Do not scaffold them.
14. **Safety-A is on hold** until the user unblocks F10. Still
    required before WAN / real arm.
15. **Language:** operator/robot stay ROS 2 Humble / Python as today.
    mlink stays Python 3 + stdlib + PyYAML. Console v1 is static
    HTML/JS in the operator image — no Node build, no extra SPA
    framework unless a later session is told to add one.
16. **Heartbeat is liveness of the operator session**, not of a
    key being held. Closing the browser must stop heartbeats so the
    500 ms watchdog trips. Blurring the tab zeros jog but may keep
    heartbeat (see F5).

---

## 8. Feature contracts

Each subsection is the brief for a later session. Substages are
optional split points: one session still does **one** substage
unless the prompt says otherwise.

### F1 — Core ROS teleop POC — STATUS: done

Two Humble containers, stamped Cartesian `TeleopCommand`, delivery
stats, Gazebo 6-DOF, MoveIt Servo, named poses, TTY keyboard.

**Read:** `teleoperation-prototype/README.md`,
`teleoperation-prototype/docs/architecture.md`,
`teleoperation-prototype/docs/implementation-plan.md` (M0–M7).

Do not rewrite this path. New features publish on the same topics.

---

### F2 — Local Zenoh — STATUS: done

Default RMW is `rmw_zenoh_cpp`. Robot runs `rmw_zenohd` on TCP 7447.
Operator is a client to `tcp/robot:7447` on `ros2_teleop_poc_net`.
Fallback: `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp ./scripts/start.sh`.

**Not done here:** two-host WAN Zenoh (that is F12, and the WAN data
plane is mlink in F8, not Zenoh-across-the-internet).

---

### F3 — mlink stages 0–3 — STATUS: done

Protocol library, loopback daemons, real eth+wifi, cable-pull.

**Read:** `mlink-transport/README.md`, `mlink-transport/docs/plan.md`.
**Verify:** `cd mlink-transport && python3 -m pytest`.

Do not change the protocol header or locked mlink decisions.

---

### F4 — Safety v0 — STATUS: done (jog path only)

`SafetyController` in `safety.py`, 500 ms watchdog, clamp 0.1 m/s /
0.3 rad/s, states `CONNECTED` / `TIMEOUT` / `SAFE STOP ACTIVATED` /
`RESTORED`. Tested by `test_watchdog.sh` and `test/test_safety.py`.

Known holes (documented, not fixed in F4): named pose bypasses the
gateway; `servo_bridge` latches last Twist if `robot_receiver` dies;
default keep-alive is `command_or_heartbeat`; a command that restores
from `TIMEOUT` can also move. Those are F10.

---

### F5 — Operator backend + web console — STATUS: remaining (next)

**Goal.** The human uses **one browser tab** on the operator PC.
The existing operator Compose service becomes a **backend**: HTTP +
WebSocket + the ROS 2 nodes that already publish `/teleop/command`
and `/teleop/heartbeat`. First prove on **localhost**. Camera in the
page can be a labeled placeholder until F6.

**Why this is first.** It is the product UI (Adamo’s dashboard is
the thing operators actually use). It unblocks mlink Stage 5 (F8),
which is explicitly blocked on this shape.

**Depends on:** F1, F2 (local Compose + Zenoh).
**Does not depend on:** mlink, Orin, Safety-A, 5G.

**Non-goals:** mlink, WAN, TLS/auth, gamepad, VR, rosbridge, a new
motion message, changing the robot container, putting the UI in a
third container.

**Where code goes:**

```text
teleoperation-prototype/
  web/                          # static HTML/CSS/JS (new)
  ros2_ws/src/teleop_demo/teleop_demo/operator_backend.py   # new
  compose.yaml                  # operator command + localhost port
  docker/Dockerfile             # only if a Python dep is required
```

TTY `keyboard_teleop.sh` **stays** as a fallback for existing tests.

**Backend contract**

- Process: a ROS 2 node in the operator container (not `sleep infinity`).
- Bind HTTP on `0.0.0.0:8090` inside Compose; publish
  `127.0.0.1:8090:8090` on the host so only localhost can open it.
- Static console at `GET /`.
- WebSocket ` /ws/session ` (name can vary; one session socket).
- While the socket is **open**, publish `/teleop/heartbeat` at the
  existing YAML rate, with **one** `session_id` shared with commands.
- On socket **close** or error: stop heartbeat immediately, publish
  one zero `TeleopCommand` (Normal Stop), then exit the keep-alive.
  Watchdog on the robot must reach `TIMEOUT` within 500 ms + slack.
- Key events from the browser map to the **same** bindings as
  `keyboard_teleop` (`w/s` x, `a/d` y, `r/f` z, `j/l` yaw, `u/o`
  roll, `i/k` pitch, `g/h` gripper, space stop). Hold = jog; release
  that key zeros that motion (match today’s 0.3 s release behavior
  as closely as the browser allows; keyup should zero without
  waiting if possible).
- Do not apply keys unless the page is focused. If the page is
  hidden (`visibilitychange` / blur): zero Twist, **keep** heartbeat
  (operator is still present). Closing the tab is not blur — that is
  socket close.
- Named-pose buttons call `/teleop/go_named_pose` (same names as
  `named_pose.sh`). Show success/failure text. Until F10, this still
  bypasses the watchdog — do not pretend otherwise in the UI.
- Telemetry panel **reads** `/teleop/state`, `/teleop/tool_pose`,
  and ack/latency if already available. It must not sit on the
  safety path (display only).
- Video panel: a box that says “camera: not wired (F6)” unless a
  later F5 substage is told to iframe a lab URL.
- Health: `GET /api/health` → 200 if ROS node is up.

**API sketch (lock the idea, names can be bikeshed in the session
if the code stays consistent):**

```text
GET  /                  static console
GET  /api/health
WS   /ws/session        client: {type:"key", key:"w", down:true|false}
                        server: {type:"state", connection_state, watchdog_state,
                                 pose, session_id, ...}
POST /api/named_pose    {"name":"fold"}
```

Browser never imports `roslib`. Backend is the only rclpy user on
the operator side for this feature.

**Stack (locked):** Python in the operator image + static JS. Prefer
stdlib `http.server` / `asyncio` + a small WS library, or `aiohttp`,
run in a thread next to `rclpy`. Do not add Node, React, or rosbridge
in F5. If a pip package is required, pin it in the Dockerfile.

**Substages (one per implementation session unless told to continue):**

| Substage | What | Acceptance |
| -------- | ---- | ---------- |
| F5.1 | HTTP server + static page in operator container, `127.0.0.1:8090`, health | Chrome on this PC loads the page; Compose still starts robot as today; `test_basic.sh` still passes via CLI |
| F5.2 | WS keys → `/teleop/command` + heartbeat while WS open | Hold `w` in the page, Gazebo tool +x; release zeros; close tab → watchdog `TIMEOUT` |
| F5.3 | Telemetry from `/teleop/state` and `/teleop/tool_pose` | Page shows CONNECTED / TIMEOUT / pose; killing WS shows TIMEOUT without using the TTY keyboard |
| F5.4 | Named-pose buttons | Click `fold` / `home` matches `named_pose.sh`; jog still works after |

**Tests to add (F5.2+):** a script or pytest that opens the WS (or
calls a small backend helper), sends `+x`, asserts robot logs /
`/teleop/state`, then drops the socket and asserts `TIMEOUT`. Do not
delete `keyboard_teleop` tests.

**Files a session must read:** this section; `teleoperation-prototype/compose.yaml`;
`teleoperation-prototype/ros2_ws/src/teleop_demo/teleop_demo/keyboard_teleop.py`;
`teleoperation-prototype/docs/architecture.md`;
`teleoperation-prototype/docs/teleop_gui_steps.txt`.

**Do not:** mlink, Orin, Safety-A, auth, exposing 8090 on `0.0.0.0`
on the host, changing `/teleop/command` fields.

---

### F6 — Video into this repo + console embed — STATUS: remaining

**Goal.** The lab WebRTC preview becomes a first-class tree in
`video/`, and the F5 console shows the camera in the **same tab**.

**Today:** `/home/nvidia/webrtc-preview-hassan` on Orin, backup at
`/home/muhammadhassan/tmp_dir2/webrtc-preview-hassan`. Pipeline
already uses `nvv4l2h264enc`. ICE is Tailscale. See `video/README.md`.

**Depends on:** F5.1 (a page to embed into). Orin + `/dev/video0` for
the live camera; `--testsrc` for CI-less lab without the USB cam.

**Non-goals:** mlink (F8), bitrate adapt (F15), multi-cam (F17),
replacing NVENC with software x264.

**Work:**

1. Copy the **scripts + yaml** into `video/` (MediaMTX binary: either
   vendor a known version or document the download; do not commit a
   random Orin binary without noting arch).
2. Keep the gst pipeline’s `nvv4l2h264enc` path.
3. Console: play the stream in the video panel (MediaMTX WHEP / the
   existing `/cam` page in an iframe is acceptable for F6; a native
   WHEP client in `web/` is better if it stays small).
4. **Lab transitional ICE** may still be Tailscale so the current
   Orin preview keeps working. Document that F8 removes Tailscale
   from the video path. Do not add new Tailscale dependencies.
5. Localhost-only demo without Orin: optional `videotestsrc` on the
   operator PC is nice-to-have, not required if Orin is the camera
   computer.

**Acceptance:** Chrome at `127.0.0.1:8090` shows the Orin camera (or
test bars) **and** still jogs the arm from the same page. `video/`
README lists start/stop. Encoder line in gst still contains
`nvv4l2h264enc`.

**Files to read:** `video/README.md`; this section; F5 `web/` page;
the lab tree `gst-publish.sh` / `mediamtx.yml` / `start.sh`.

---

### F7 — Orin HW encode verify / efficiency — STATUS: partial

**Goal.** Prove the pixels that reach the operator used Jetson HW
encode, and cut obvious CPU waste on the camera path.

**Already true in the lab gst line:** `nvv4l2h264enc` after
`nvvidconv` into NVMM NV12. USB camera is MJPG → **software
`jpegdec`** → NVENC. That JPEG decode is the main remaining CPU
cost.

**Work (pick what the session can measure on the Orin):**

1. Confirm with logs / `tegrastats` / encoder debug that
   `nvv4l2h264enc` is the active encoder (not a silent fallback to
   `x264enc`).
2. If the camera can deliver a NVENC-friendly raw format, skip
   JPEG; else try `nvjpegdec` / `nvv4l2decoder` instead of `jpegdec`
   if available on that L4T.
3. Do not transcode twice. Do not add a CPU encoder “just in case.”
4. Keep bitrate caps (today ~1.5 Mbps 480p, ~2.5 Mbps 720p).
5. Write the measured result in `video/README.md` (CPU %, encoder
   name, resolution, bitrate).

**Non-goals:** bitrate adapt (F15), mlink, changing the console.

**Depends on:** F6 (tree in git) or the lab tree if F6 is not done
and the user points the session at the Orin path.

---

### F8 — mlink Stage 5 (apps on localhost) — STATUS: blocked on F5

**Goal.** Operator backend and robot-side apps talk to mlink on
`127.0.0.1`. WAN copies are eth+wifi (and 5G if F9 exists). Apps
never see three public IPs.

**Blocked until:** F5 backend is running (a real localhost app face,
not TTY `docker exec`). Stage 4 dongle is **not** required.

**Locked approach (see decision 9–10):**

```text
browser → backend (HTTP/WS, local WebRTC)
            │ UDP 127.0.0.1  traffic_class=control
            │ UDP 127.0.0.1  traffic_class=media
         mlink-op  == copies ==  mlink-edge
            │ control → robot UDP shim → local ROS /teleop/*
            │ media   → RTP/MediaMTX or a local WHEP helper
```

- Do **not** run `rmw_zenoh` TCP through mlink.
- Keep Zenoh/Cyclone for **on-host** ROS (operator process ↔ nothing
  remote; robot processes on the Orin).
- Add a small **control bridge**: serialize `TeleopCommand` /
  `TeleopHeartbeat` / ack / state (CDR or a documented compact
  payload) as mlink datagrams. MTU budget is **1440** bytes; these
  messages fit.
- Media: RTP from `nvv4l2h264enc` into mlink `media` class; operator
  mlink emits RTP to a localhost player the console already uses.
  Do not ICE the browser across bonded NICs.
- Do not rewrite `mlink-transport/` except app-facing ports/docs.
- Control must not wait behind video (already in mlink queues).

**Acceptance:** kill Ethernet (or `down eth` on mlink); keyboard jog
and (if F6 is in) video continue on Wi-Fi; SSH over Wi-Fi/Tailscale
still works; pytest still green.

**Read:** `mlink-transport/docs/plan.md` Stage 5; this section; F5
backend listen ports.

**Resume only with the Stage 5 prompt in `mlink-transport/docs/plan.md`
plus this F8 section.**

---

### F9 — mlink Stage 4 (5G) — STATUS: blocked (no dongle)

Config-only third path on the Orin (`wwan0` or the real ifname).
No protocol change. No ROS/WebRTC work in that session.

**Read / resume:** `mlink-transport/docs/plan.md` Stage 4.

---

### F10 — Safety-A (local harden) — STATUS: on hold

**Do not start** until the user unblocks this feature.

Required before WAN / real arm. Same two Compose containers. No new
architecture. Details: `safety/coverage-analysis.md`,
`safety/zone1-implementation-plan.md`.

**Must do, in order:**

1. Named pose refused/cancelled on `TIMEOUT` / `SAFE STOP`.
2. `servo_bridge` zeros if `/cmd_vel_safe` is silent (~200–500 ms)
   so `robot_receiver` death cannot keep jogging.
3. Restart hold: a command that itself restores from `TIMEOUT` is
   `HELD`; the next command may move.
4. Space / zero Twist cancels an in-flight named pose.
5. Optional: joint-velocity “stationary” flag on `/teleop/state`.

**Acceptance:** existing watchdog tests plus pose-during-timeout
does not move; `pkill -f robot_receiver` while jogging → tool pose
holds.

**Out:** hardware E-stop, STO, ISO 10218 product, Zone 2 locomotion.

---

### F11 — Safety-B (WAN) — STATUS: remaining (after F10)

- Robot default `watchdog_keep_alive: heartbeat_only`.
- Heartbeat `session_id` must match the command session.
- Software E-stop latch (`/teleop/estop`) + `/teleop/reset`.
- Reset required after timeout / E-stop before jog.
- Console buttons for E-stop and Reset (must not be the only stop:
  watchdog still fires if the tab dies).

Hardware mushroom / STO remains the integrator’s job on a real arm.

---

### F12 — WAN / Jetson ROS split — STATUS: remaining

Today operator and robot containers share one host. Product: robot
stack on Orin, backend on the operator PC, mlink between them (F8).

This is **not** “run rmw_zenoh across the public internet.” F8 is
the WAN. F12 is packaging: robot image/launch on Orin, operator
backend on the PC, documented IPs, no Compose bridge as the WAN.

**Depends on:** F5, F8, and F10 before anyone jogs a real/WAN arm.

---

### F13 — Gamepad — STATUS: remaining (after F5)

Same backend API as keys. Browser Gamepad API → Twist on the WS
(or a `type:"twist"` message). Do not add a ROS `joy` node as the
product path (optional fallback later). Deadman: no buttons held →
zero Twist. Map a bumper to deadman if we need “hold to move.”

VR is deferred.

---

### F14 — Video-freshness stop — STATUS: remaining (after F6)

If the operator has no fresh frame for N ms (start with **500–1000 ms**,
measure in F19), the robot must protective-stop the same as a dead
heartbeat. Implement on the **robot** (or backend → heartbeat
suppress), not only as a red banner in the UI.

Adamo-style: cannot drive what you cannot see. Do not fake this
with a CSS overlay.

**Depends on:** F6 (a real frame clock), F10 preferred so the stop
covers named pose too.

---

### F15 — Bitrate adaptation — STATUS: remaining

When media uses mlink or a real WAN, drop encoder bitrate (and
maybe resolution) when the media path’s loss/RTT is high. Hook is
gst `nvv4l2h264enc` bitrate + mlink path stats. Not in Stages 1–3
of mlink by design.

---

### F16 — Console TLS / auth — STATUS: remaining

Before the UI is reachable beyond this PC: bind TLS, a session
token or basic auth, and keep 8090 off the public internet. v1 can
be a shared lab password. Not OAuth, not SOC2.

F5 must stay localhost-only so this can wait.

---

### F17 — Multi-camera — STATUS: remaining

Second MediaMTX path / second gst pipeline, tabs or a grid in the
console. After one camera is stable. Wrist + scene is the usual
teleop pair; do not build a matrix product in the first session.

---

### F18 — Real hardware arm — STATUS: remaining

Replace Gazebo + `arm_controller` with the vendor driver **behind**
the same `/cmd_vel_safe` / named-pose gate. Do not let the vendor
UI publish around the gateway. Requires F10 at minimum, F11 if WAN.

---

### F19 — Benchmarks — STATUS: remaining

Measure, do not `netem` as the product proof:

- Local Docker command one-way / RTT (already ~ms)
- mlink eth vs wifi vs (later) 5G: `max_gap_ms`, loss, which
  `path_id` won (`mlink-transport/docs/latency_comparison.md`)
- Watchdog trip time after WS close
- Video motion-to-photon if we can; at least fps and bitrate
- CPU on Orin while encoding + jogging

Scripts with numbers and logs. Put results next to the feature
(teleop `docs/` or `mlink-transport/docs/` or `video/`).

---

### F20 — mlink payload encryption — STATUS: remaining

v1 is plaintext UDP. Later: DTLS or a pre-shared key on the
payload. Do not invent this during F3–F9. Adamo’s AES-256 claim is
not a v1 gate.

---

### F21 — CGNAT relay — STATUS: remaining

5G on the Orin is often not reachable. Options: operator has a
public IP, or a small relay VPS. This is **ops + extra mlink peer
or a UDP relay**, not a rewrite of the protocol. Only when F9 is
real. Tailscale is still not the data path.

---

### Deferred (do not implement)

- **Fleet / hired operators** — single-arm product first.
- **Session recording** — teammate / LeRobot; we do not duplicate it.
- **VR**
- **SOC2, ISO 10218, IEC 61508, safety PLC**
- **Zone 2 locomotion, force limiting, perception SSM, energy
  isolation** — see `safety/coverage-analysis.md` “out of scope”

---

## 9. Session order

Do these in order unless a blocker is lifted out of sequence.

| Order | ID | When |
| ----- | -- | ---- |
| 1 | F5.1 → F5.4 | Now. Localhost console. |
| 2 | F6 | Camera in the same tab. |
| 3 | F7 | Short; can pair with F6 if the session is on the Orin. |
| 4 | F8 | mlink Stage 5; unblocked by F5. |
| 5 | F10 | When the user lifts the Safety-A hold. **Before WAN/real arm.** |
| 6 | F9 | When the 5G dongle is on the Orin. Independent of F8. |
| 7 | F12 | Robot processes on Orin, backend on PC. |
| 8 | F11, F14 | WAN safety + video-freshness. |
| 9 | F13, F15, F16, F17, F19 | Product polish; order can flex. |
| 10 | F18 | Real arm. |
| 11 | F20, F21 | Encryption / relay as needed. |

F5 is the only feature a new session should start without asking.

---

## 10. Exact prompts for implementation sessions

### F5.1 — HTTP page in the operator container

```text
Read /home/muhammadhassan/robots/IMPLEMENTATION.md from the start.
Implement Feature F5.1 only (HTTP server + static console page in the
existing operator Compose service, localhost 127.0.0.1:8090, health).
Do not implement F5.2 keys, camera, mlink, or Safety-A.
Do not add rosbridge, Node, or React.
Keep keyboard_teleop.sh and existing test_*.sh working.
Do not git commit unless asked. Stop after F5.1 and show how to open
the page.
```

### F5.2 — Browser keys + heartbeat

```text
Read /home/muhammadhassan/robots/IMPLEMENTATION.md from the start.
F5.1 is done. Implement F5.2 only: WebSocket keys -> /teleop/command
and heartbeat while the socket is open. Same key map as keyboard_teleop.
Close tab must stop heartbeat so the 500 ms watchdog trips. Blur zeros
jog but keeps heartbeat.
Do not do named poses, telemetry polish, camera, mlink, or Safety-A.
Keep CLI tests green. Do not git commit unless asked.
```

### F5.3 / F5.4

Same pattern: read this file, implement only that substage, stop.

### F6 — video in repo + embed

```text
Read /home/muhammadhassan/robots/IMPLEMENTATION.md (F6) and
video/README.md. Import the Orin WebRTC preview into video/ (scripts +
config; document the MediaMTX binary). Embed the camera in the F5
console. Keep nvv4l2h264enc. Do not put video through mlink. Tailscale
ICE may remain as a documented lab shortcut. Do not git commit unless
asked.
```

### F8 — mlink Stage 5

Use the Stage 5 prompt in `mlink-transport/docs/plan.md` **and**
read F8 in this file (localhost UDP control/media, no Zenoh-through-
mlink, no mlink rewrite).

### F9 — 5G dongle

Use the Stage 4 prompt in `mlink-transport/docs/plan.md`.

### F10 — Safety-A (only after user unblocks)

```text
Read /home/muhammadhassan/robots/IMPLEMENTATION.md F10,
safety/zone1-implementation-plan.md, and safety/coverage-analysis.md.
Safety-A is unblocked. Implement the Zone 1 Safety-A order only
(named-pose gate, servo_bridge latch, restart hold, space cancels
pose). Local Docker. Do not start Safety-B, WAN, or mlink. Keep
existing tests and add the pose-during-timeout and receiver-kill
cases. Do not git commit unless asked.
```

---

## 11. Key decisions (summary)

| Decision | Rationale |
| -------- | --------- |
| Backend + browser, not ROS desktop | Product UI; unblocks mlink Stage 5; matches how operators actually work |
| Evolve operator Compose service | One operator-side ROS graph; no third container |
| Browser never speaks ROS | We can add auth and mlink without exposing the graph |
| Zenoh stays on-host; WAN is mlink UDP | mlink is a datagram pipe; stuffing TCP Zenoh through it is the wrong layer |
| WebRTC terminates at localhost | Browser must not ICE across three NICs |
| Tailscale = SSH only | SSH must survive path failure; Adamo-style bonding is mlink |
| Safety stays on the robot | Watchdog must work if the operator process dies |
| Safety-A on hold but gated before WAN | User paused integration; WAN still must not ship the named-pose bypass |
| NVENC already in lab gst | F7 is verify + JPEG-decode cost, not “add HW encode from zero” |
| Fleet / recording / VR / cert deferred | v1 is one operator, one arm, one console |

---

## 12. Open questions (do not block F5)

These can wait until the feature that needs them. Do not stall F5.

1. **MediaMTX vs a smaller WHEP helper in v1** — F6 can iframe
   MediaMTX; F8 may want a localhost-only player.
2. **Control payload encoding** — ROS CDR vs a packed struct for
   the F8 bridge. Pick in the F8 session; both fit in 1440 bytes.
3. **Who runs mlink** — host processes beside Compose (today’s
   daemons) vs a Compose sidecar. Default: **keep host daemons**
   until F8 proves otherwise (`SO_BINDTODEVICE` is simpler on the
   host).
4. **Gripper on Normal Stop** — hold (current) vs close. Safety-A
   plan says hold. Console should match.

---

## 13. Planner checklist (after an implementation session)

- [ ] Feature / substage matches the contract above
- [ ] Locked decisions still held
- [ ] `STATUS` updated in §3
- [ ] Feature-dir README / plan still points here
- [ ] Next-session prompt is accurate
- [ ] Did not start the next ID in the same session unless asked
