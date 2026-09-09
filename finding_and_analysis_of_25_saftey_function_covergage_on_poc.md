# Finding and analysis: 25 safety-function coverage on the teleop POC

Scope is the robot-side safety path that already exists on `main` (M0–M7 plus local Zenoh). No WAN/Jetson work, no new nodes, no implementation in this document.

Command path today:

```text
keyboard / operator_command
  → /teleop/command + /teleop/heartbeat
  → robot_command_receiver (session/seq stats + SafetyController)
  → /cmd_vel_safe + /gripper_safe + /teleop/state + /teleop/ack
  → servo_bridge
  → MoveIt Servo → arm_controller → Gazebo
```

Named poses are a **second motion path**: `/teleop/go_named_pose` → `named_pose` → `move_group` → `arm_controller`. That path never touches `/teleop/command` or `/cmd_vel_safe`.

The consultant file (`robotics_safety_functions_25.txt`) titles “25” functions; the visible boxes are **23**. Mapping is against those 23.

Sources used (safety-related code only):

- `teleoperation-prototype/ros2_ws/src/teleop_demo/teleop_demo/safety.py`
- `teleoperation-prototype/ros2_ws/src/teleop_demo/teleop_demo/robot_receiver.py`
- `teleoperation-prototype/ros2_ws/src/teleop_demo/teleop_demo/servo_bridge.py`
- `teleoperation-prototype/ros2_ws/src/teleop_demo/teleop_demo/named_pose.py`
- `teleoperation-prototype/ros2_ws/src/teleop_demo/teleop_demo/delivery.py`
- `teleoperation-prototype/ros2_ws/src/teleop_demo/teleop_demo/operator_heartbeat.py`
- `teleoperation-prototype/ros2_ws/src/teleop_demo/teleop_demo/keyboard_teleop.py`
- `teleoperation-prototype/ros2_ws/src/teleop_demo/teleop_demo/commands.py`
- `teleoperation-prototype/config/teleop.yaml`
- `teleoperation-prototype/ros2_ws/src/teleop_demo/config/servo.yaml`
- `teleoperation-prototype/ros2_ws/src/teleop_demo/config/joint_limits.yaml`
- `teleoperation-prototype/ros2_ws/src/teleop_demo/config/teleop_arm.srdf`
- `teleoperation-prototype/ros2_ws/src/teleop_demo/launch/robot_sim.launch.py`
- `teleoperation-prototype/docs/architecture.md`
- `teleoperation-prototype/scripts/test_watchdog.sh`
- `teleoperation-prototype/ros2_ws/src/teleop_demo/test/test_safety.py`

---

## How current mechanisms map (or fail to map)

### 1. Heartbeat + 500 ms watchdog → `/cmd_vel_safe`

**What it is.** `SafetyController` in `safety.py`, driven by `robot_receiver` at `controller_rate_hz` (50 Hz). Keep-alive is either a valid command or a heartbeat (`watchdog_keep_alive: command_or_heartbeat` in `config/teleop.yaml`). If nothing arrives for **500 ms**, twist on `/cmd_vel_safe` is zeroed and the state becomes `TIMEOUT` + `SAFE STOP ACTIVATED`. Separately, if still `CONNECTED` but no command for **200 ms**, the jog rate is zeroed without tripping the watchdog (rates are not latched).

**Maps onto:** Normal Stop (partial: command silence zeros jog), Protective Stop (software stand-in), Loss of “operator present” (not Loss of Power).

**Does not map onto:** hardware protective stop, E-stop, STO, or any power-side isolation. It only gates **this** publisher’s Twist.

**Gaps that matter:**

- Default policy is `command_or_heartbeat`. A stuck command publisher keeps the watchdog alive. `heartbeat_only` exists and is tested, but is not the running default.
- Heartbeats are **not** bound to `session_id`. Any `/teleop/heartbeat` resets the timer (`robot_receiver.heartbeat_callback`).
- Invalid NaN/Inf commands do not keep the watchdog alive (good). Clamped commands do.
- `servo_bridge` **latches** the last `/cmd_vel_safe` and republishes it at 50 Hz with a **fresh stamp** on `/servo_node/delta_twist_cmds`. If `robot_receiver` dies while the last twist was non-zero, Servo keeps jogging. Servo’s own `incoming_command_timeout: 0.2` only trips if **`servo_bridge`** dies, not if the safety node dies.
- Gripper is **not** zeroed on timeout. Last `/gripper_safe` is held.

### 2. Session id + sequence + ack / loss / out-of-order

**What it is.** `TeleopCommand.session_id` + `sequence`. Receiver resets `DeliveryTracker` / `LatencyStats` on session change and logs `SESSION START/RESET`. Ack echoes seq/session/stamps/`disposition`. Loss, duplicates, and out-of-order are **counters only**.

**Maps onto:** telemetry for M3/M10. Not a safety function on the consultant list.

**Does not do:**

- Reject duplicate seq (duplicate still calls `safety.on_command` and can move the arm).
- Reject out-of-order or stale seq.
- Require a matching heartbeat `session_id`.
- Bind one operator session (keyboard shares one id; `operator_heartbeat` mints a **different** uuid from `operator_command`).
- Use ack as a closed-loop enable. Motion does not wait for ack.
- Age-out commands using `stamp` (one-way ns is logged, not used for accept/reject). Shared-host clocks make that number valid locally; it will not be a safety input on WAN.

This is **delivery instrumentation**, not an interlock.

### 3. Safe-stop / `TIMEOUT` / `RESTORED`

**What it is.** States published on `/teleop/state` and logged as `SAFETY STATE=…`:

| State | Meaning in this POC |
|---|---|
| Startup `SAFE STOP ACTIVATED` | No motion until first keep-alive |
| `CONNECTED` | Watchdog OK; clamped twist may flow |
| `TIMEOUT` + `SAFE STOP ACTIVATED` | Keep-alive older than 500 ms; `/cmd_vel_safe` forced to zero; last twist discarded |
| `RESTORED` | Keep-alive returned after timeout; twist stays zero until a fresh command |

**Maps onto:** Protective Stop (software), Start/Restart Interlock (partial), Reset (partial).

**Interlock is weaker than the docs imply.** Architecture says motion resumes only on a fresh command after `RESTORED`. That is true if a **heartbeat** restores first (`test_watchdog.sh` does this). If `keep_alive` is `command_or_heartbeat`, a command that arrives in `TIMEOUT` **restores and executes in the same callback** (`on_command` → `_register_keep_alive` sets `RESTORED` → not `TIMEOUT` anymore → `_awaiting_fresh_command` consumes that same command). There is no operator Reset distinct from “send another jog.”

Spacebar is a **zero Twist** on `/teleop/command`, not a latched stop. If the operator link is already dead, space does nothing; the watchdog already zeroed output.

### 4. MoveIt Servo + joint limits / planning / named poses

**Servo** (`config/servo.yaml`), fed only from `servo_bridge`:

- Cartesian speed units, `incoming_command_timeout: 0.2`, `halt_all_joints_in_cartesian_mode: true`
- Singularity thresholds (80 / 120)
- `joint_limit_margin: 0.1`
- Collision check at 10 Hz (`check_collisions: true`)
- URDF joint limits; MoveIt `joint_limits.yaml` velocity/acceleration

**Named poses** (`named_pose.py` + SRDF + `arm_kinematics.NAMED_POSES`): OMPL `RRTConnect`, vel/acc scale 0.4, joint-goal constraints ±0.05 rad. Stops Servo, plans, starts Servo again.

**Maps onto:** Normal Stop (Servo halt on input timeout — but only if bridge stops), Contact-Force Limiting (not really: collision *check*, not force), Speed & Separation (not really: speed cap 0.1 m/s on the **teleop** clamp, no separation), Mode Selection (not really: two motion sources, no mode switch).

**Does not cover:** certified collision avoidance. SRDF **disables** several non-adjacent collision pairs because visual boxes overlap at home. Scene collision is empty in this Gazebo world. Servo is not a safety PLC.

### 5. Named-pose path that does not use `/teleop/command` (bypass)

**This is a real bypass of the safety gateway.**

`/teleop/go_named_pose` is a service on node `named_pose`. It does not subscribe to `/teleop/command`, `/teleop/heartbeat`, or `/teleop/state`. It does not publish `/cmd_vel_safe`. It writes the arm through `move_action` while Servo is stopped.

Consequences:

- Named pose **runs during `TIMEOUT` / `SAFE STOP ACTIVATED`**.
- No session, seq, or ack.
- After the pose, Servo is restarted. `servo_bridge` may still be holding a last Twist and immediately resume jogging.
- `./scripts/named_pose.sh` is callable from the operator container with no extra enable.

Jog path: safety sits in front of Servo (as designed). Named-pose path: safety is not on the path.

---

## Function-by-function

For each consultant function: meaning in this teleop/arm context, coverage now, where it lives if present, what it does not cover, and future in this POC (`keep as-is` / `extend` / `add later`).

### Zone 1 — Stopping & Restart

**1. Normal Stop [MAND] — partial — extend (Safety-A)**

Operator-commanded stop of jog: spacebar / `stop` direction / 200 ms `command_timeout_ms` zeros `/cmd_vel_safe` while staying `CONNECTED`. Keyboard also zeros 0.3 s after key release. Servo can halt if `delta_twist_cmds` stop.

- **Where:** `keyboard_teleop.py` (space / 0.3 s release), `safety.py` (`command_timeout_s`), `/cmd_vel_safe`, Servo `incoming_command_timeout` (only if `servo_bridge` stops publishing).
- **Does not cover:** Category 1/2 stop, deceleration profile, gripper, named-pose trajectories in flight (`named_pose` has no cancel), or a latched “stopped” mode. Space is just another `TeleopCommand`.
- **Future:** keep the rate-zero behavior. Add an explicit stop that zeros `servo_bridge`’s latch and optionally cancels `move_group`. Local Docker first.

**2. Protective Stop [MAND] — partial — extend (Safety-A, required before WAN)**

Watchdog 500 ms → `TIMEOUT` + `SAFE STOP ACTIVATED` → zero `/cmd_vel_safe`. Startup latches the same stop. Tested in `test/test_safety.py` and `scripts/test_watchdog.sh`.

- **Where:** `safety.py` (`watchdog_timeout_s`), `robot_receiver.py` (50 Hz `_safety_tick`), `/teleop/heartbeat`, `/teleop/state`.
- **Does not cover:** safety-rated I/O, STO, named-pose motion, `servo_bridge` latch after receiver death, gripper.
- **Future:** keep watchdog. Gate named pose on this state. Make `servo_bridge` zero if `/cmd_vel_safe` is silent. Local Docker first; WAN must not ship without this.

**3. E-Stop [COND] — not present — add later (Safety-B, software only)**

No E-stop topic, button, or latch independent of heartbeat.

- **Where:** nowhere.
- **Does not cover:** hardware mushroom, dual-channel, Category 0/1.
- **Future:** optional `/teleop/estop` (or equivalent) that latches `SAFE STOP` until Reset. Software-only, after local harden. Hardware E-stop is out of scope for this prototype.

**4. Start/Restart Interlock [MAND] — partial — extend (Safety-A / Safety-B)**

Startup is stopped. After timeout, heartbeat restore holds motion until a **later** command. A command in `TIMEOUT` with default keep-alive **restores and moves immediately**. No enable device, no two-step Reset-then-Start, no check that named pose is idle.

- **Where:** `safety.py` (`RESTORED`, `_awaiting_fresh_command`), `/teleop/state`.
- **Does not cover:** two-step reset, enable device, named-pose idle check.
- **Future:** treat `RESTORED` as hold until an explicit enable or a command **after** restore. Default WAN policy: `heartbeat_only`. Local first.

**5. Reset [COND] — partial — extend (Safety-B)**

`RESTORED` is the reset analogue. It is automatic on keep-alive, not an operator Reset, and it is not required before the restoring command can move.

- **Where:** `safety.py` `_register_keep_alive` when `_ever_connected`.
- **Does not cover:** operator Reset distinct from keep-alive.
- **Future:** optional explicit reset on `/teleop/state` or a service. Not needed for local keyboard+Gazebo.

**6. Monitored Stationary Position [COND] — not present — add later / likely out of scope**

Zero Twist is commanded; there is no encoder/torque standstill monitor. `arm_controller` has `stopped_velocity_tolerance: 0.05` as a **trajectory goal** constraint, not a safety monitor.

- **Where:** not as a safety function. `arm_controllers.yaml` goal constraint only.
- **Future:** out of scope for Gazebo POC. On hardware, this is a drive/safety-controller feature, not a ROS node.

---

### Zone 2 — Stability & Locomotion

The robot is a **fixed-base tabletop 6-DOF arm** in Gazebo. There is no mobile base, no IMU-based stability, no elevation.

**7. Loss-of-Stability Risk Reduction [COND] — not present — keep as-is (N/A)**

Would mean reducing tip-over or base-shift risk while the arm moves. This POC has a fixed Gazebo pedestal, not a mobile or unbalanced platform.

**8. Dynamic Stability Control [COND] — not present — keep as-is (N/A)**

Would mean closed-loop balancing or CoG management during motion. Not applicable to a bolted/simulated tabletop arm.

**9. Onset-of-Instability Detection [COND] — not present — keep as-is (N/A)**

Would mean detecting early tilt/slip. No IMU or base wrench in this stack.

**10. Unrecoverable Loss-of-Stability Detection [COND] — not present — keep as-is (N/A)**

Would mean detecting a fall/tip that cannot be recovered and forcing a stop. No such sensor path.

**11. Elevation-Change Detection [COND] — not present — keep as-is (N/A)**

Would mean detecting ramps, drops, or lift. The arm does not locomote.

**12. Navigation Risk Reduction [COND] — not present — keep as-is (N/A)**

Would mean reducing collision/nav risk of a mobile base. There is no `/cmd_vel` for wheels; Twist is a tool jog.

These are mobile-robot / AGV functions. Do not invent them for this arm POC. Tip-over of a real pedestal is a mechanical/install issue, not a teleop node.

---

### Zone 3 — Perception & Collaboration

**13. Speed & Separation Control [UNTAGGED] — partial (speed only) — keep as-is locally; do not fake SSM**

Would mean reducing tool/base speed as a person or obstacle gets closer. This POC only has a fixed Cartesian speed clamp.

- **Where:** `max_linear_velocity: 0.1` m/s, `max_angular_velocity: 0.3` rad/s (`safety.py` / `teleop.yaml`). Named poses use 0.4 scaling.
- **Does not cover:** distance-based speed, collaborative workspace, ISO/TS 15066 SSM.
- **Future:** keep clamps. Do not add fake “separation” without sensors. Perception is out of the first WAN target.

**14. Contact-Force Limiting [COND] — not present — keep as-is (out of scope)**

Would mean limiting contact force/torque if the arm hits something.

- **Where:** URDF `effort` limits and Gazebo PID are simulation, not force control. No F/T sensor. Servo collision check is proximity, not contact force.
- **Future:** out of scope for this prototype.

**15. Perception Safeguarding [COND] — not present — keep as-is (out of scope)**

Would mean using sensors (camera, lidar, scanner) to inhibit motion in occupied zones.

- **Where:** nowhere. Camera/video was dropped from the plan.
- **Future:** out of scope for the first WAN target.

---

### Zone 4 — Power & Interfaces

All four are **not present** in software. The “power system” is Docker + Gazebo.

**16. Hazardous Energy Isolation [MAND] — not present — out of scope**

Would mean lockout/tagout or STO so motors cannot be energized. Software stop is not LOTO / STO. `docker compose stop` is not energy isolation.

**17. Stored-Energy Control [MAND] — not present — out of scope**

Would mean controlling residual energy (brakes, gravity load, pressure). No brake, gravity-compensation safety, or pressure dump. Gazebo joints are simulated.

**18. Loss of Power Protection [MAND] — not present as power; partial as comms loss — extend comms only**

Would mean a defined behavior on mains/24 V loss (brakes, STO). Watchdog covers **loss of operator messages**, not power. Process crash of `robot_receiver` is **not** equivalent (`servo_bridge` latch). Container kill of the whole robot stack stops Gazebo too — that is not a model of hardware power loss.

- **Future:** treat comms-loss and **safety-node death** as protective stop (Safety-A). Real loss-of-power / STO is hardware, out of scope.

**19. Minimum-Charge Interlock [MAND] — not present — out of scope**

Would mean inhibiting motion below a battery threshold. No battery. Jetson later still should not pretend ROS is a BMS.

---

### Zone 5 — System, Fleet & AI

**20. Mode Selection [COND] — not present (two concurrent motion sources) — extend (Safety-A)**

Would mean mutually exclusive operating modes (e.g. jog vs planned motion vs idle) with an interlock so only one motion writer is enabled.

- **Where:** no Auto / Manual / Teach. Keyboard jog, scripted CLI, and named pose can overlap. Named pose stops Servo (a mutex for the **controller writer**), not a mode selector. Watchdog does not disable `/teleop/go_named_pose`.
- **Future:** one motion mode at a time: jog XOR named pose; named pose refused unless `CONNECTED` (or an explicit “planning” mode). Local Docker first.

**21. Fleet Safety Command [MAND] — not present — keep as-is (N/A)**

Would mean a site/fleet abort that stops every robot. This POC is a single arm. Do not add a fake fleet layer.

**22. AI-Based Safety Control [COND] — not present — keep as-is (N/A)**

Would mean an ML/AI component on or beside the safety path. None exists. Do not put one there.

**23. Attachment/Payload Stability Check [COND] — not present — keep as-is (N/A)**

Would mean verifying payload mass/CoG/attachment before motion. Gripper open/close is a 0..1 command on `/gripper_safe`. No payload mass, CoG, or attachment detect.

---

## Coverage table

| # | Consultant function | Tag | Current POC | Gap |
|---|---|---|---|---|
| 1 | Normal Stop | MAND | **Partial.** Space / zero Twist / 200 ms command timeout → `/cmd_vel_safe` = 0 | Not latched; doesn’t cancel named pose; gripper held |
| 2 | Protective Stop | MAND | **Partial.** 500 ms watchdog → `TIMEOUT` + zero Twist | Software only; named-pose bypass; receiver-death latch in `servo_bridge` |
| 3 | E-Stop | COND | **Not present** | No independent latch |
| 4 | Start/Restart Interlock | MAND | **Partial.** Startup stop; heartbeat `RESTORED` holds; command-after-timeout can move immediately | No two-step reset; no enable device |
| 5 | Reset | COND | **Partial.** Automatic `RESTORED` | No operator Reset |
| 6 | Monitored Stationary Position | COND | **Not present** | Zero command ≠ standstill monitor |
| 7–12 | Stability / locomotion / nav | COND | **Not present** (N/A, fixed-base arm) | Do not add |
| 13 | Speed & Separation Control | — | **Partial.** Speed clamp only | No separation / occupancy |
| 14 | Contact-Force Limiting | COND | **Not present** | No F/T |
| 15 | Perception Safeguarding | COND | **Not present** | Camera out of scope |
| 16 | Hazardous Energy Isolation | MAND | **Not present** | Not a ROS function |
| 17 | Stored-Energy Control | MAND | **Not present** | Not a ROS function |
| 18 | Loss of Power Protection | MAND | **Not present** (comms loss only) | Watchdog ≠ power; node death hole |
| 19 | Minimum-Charge Interlock | MAND | **Not present** | No battery |
| 20 | Mode Selection | COND | **Not present** | Jog + named pose concurrent |
| 21 | Fleet Safety Command | MAND | **Not present** (N/A, one robot) | Do not add |
| 22 | AI-Based Safety Control | COND | **Not present** (N/A) | Do not add |
| 23 | Attachment/Payload Stability | COND | **Not present** | Gripper only |

**Mechanism vs list (explicit):**

| Mechanism | Closest consultant boxes | Verdict |
|---|---|---|
| Heartbeat + 500 ms watchdog → `/cmd_vel_safe` | Protective Stop, (comms) Loss of Power, Normal Stop (idle) | Software protective stop for **jog only** |
| Session + seq + ack / loss / OOO | *(none)* | Telemetry; **not** an interlock |
| `SAFE STOP` / `TIMEOUT` / `RESTORED` | Protective Stop, Start/Restart Interlock, Reset | Real state machine; restart too weak; bypassed by named pose |
| MoveIt Servo + URDF/joint limits + collision + named-pose planning | Speed (cap), weak collision, planning limits | Motion constraint layer, **not** Zone 1 stop |
| `/teleop/go_named_pose` | Mode Selection (missing) | **Bypasses** 1–5 for that motion |

---

## Good enough for local keyboard + Gazebo POC

These are already adequate for **one host, Docker, keyboard, Gazebo, no people in a real cell**:

- Startup latched stop
- Clamp 0.1 m/s / 0.3 rad/s and NaN/Inf reject
- 200 ms jog-zero so rates do not latch
- 500 ms watchdog zeros `/cmd_vel_safe` and will not replay the last Twist after heartbeat restore
- `/teleop/state` visible for a human watching logs
- MoveIt joint limits, singularity halt, and (imperfect) self-collision in sim
- Named poses planned at 0.4 scale, not raw joint dumps
- Tests: `test_safety.py`, `test_watchdog.sh`, `test_named_pose.sh`

Do **not** block local demo work on ISO 10218, E-stop hardware, SSM, or fleet.

Known local caveats (document, don’t freeze the demo): named pose ignores watchdog; `command_or_heartbeat` default; `servo_bridge` latch if receiver is killed; session/seq do not reject bad packets; SRDF disables some collisions.

---

## Must exist before WAN / Jetson (M8 step 2), ranked

These are still prototype-grade (not SIL). They close holes that WAN makes likely: delay, reorder, process split, two hosts, no shared clock.

1. **Protective stop must cover every motion writer.** Refuse `/teleop/go_named_pose` unless watchdog is `OK` (or cancel pose on `TIMEOUT`). Highest-impact bypass today.
2. **`servo_bridge` must not keep a live Twist if `/cmd_vel_safe` goes silent.** Watchdog is useless if the receiver process dies and the bridge keeps stamping the last command. Local Docker can prove this with a receiver kill; required before Jetson.
3. **`heartbeat_only` as the WAN keep-alive policy.** Default `command_or_heartbeat` lets a delayed or stuck command stream look like liveness. Keyboard already publishes both topics.
4. **True restart hold:** after `TIMEOUT`, first restore (heartbeat) must not move; a command that itself restores must be `HELD` until `CONNECTED` again. Matches Start/Restart Interlock without a new architecture.
5. **Session + seq as a gate, not just stats.** Bind heartbeat `session_id` to the command session; drop duplicates and (optionally) old seq. Ack stays telemetry. WAN will reorder and retry.
6. **Do not use one-way `stamp` age as a safety trip** until clocks are defined. Today it is correctly unused for accept/reject. Keep it that way on WAN; use RTT/watchdog instead (plan already says this).
7. **Software E-stop latch** (optional but cheap): one latched topic/service that zeros jog **and** rejects named pose until Reset. Not a substitute for a hardware button on a real arm.

Items 1–4 are the bar. 5–7 are strongly recommended before anyone jogs a real Jetson arm.

---

## Out of scope for this prototype

Do not invent a SIL / ISO 10218 / IEC 61508 / ISO/TS 15066 product.

- Hardware E-stop, dual-channel, STO, safety PLC, PLd/e
- Hazardous energy isolation, stored energy, battery / minimum-charge
- Standstill monitoring in the drive
- Mobile stability, navigation, fleet abort
- Speed & separation with people, force limiting, vision safeguarding
- AI on the safety path
- Certified collision avoidance (MoveIt `check_collisions` is a demo aid)
- Camera/video (already dropped)
- Rewriting M0–M8 transport, Gazebo, or keyboard wire

Mandatory Zone 4 items and Fleet Safety Command are **mandatory on the consultant diagram for a product robot**, not for this POC. Treat them as “acknowledged, not in prototype.”

---

## Suggested milestone split (does not rewrite M0–M8)

Keep M8 step 1 (local Zenoh) as transport. Do **not** start M8 step 2 in this split. Safety work sits **beside** M4/M7 nodes.

### Safety-A — local harden (same two containers, Gazebo)

Goal: the safety gateway is actually the only motion path, including named poses and bridge latch.

- Gate `named_pose` on `/teleop/state` (or a tiny query into the same `SafetyController` instance / topic).
- Zero `servo_bridge` if `/cmd_vel_safe` stale (reuse `incoming_command_timeout` idea on the bridge).
- Optional: switch keyboard path to `heartbeat_only` in `teleop.yaml` (scripted `operator_command` tests may keep `command_or_heartbeat` via param).
- Tighten `RESTORED` so a timeout-era command is `HELD`.
- Session/seq reject duplicates (still on `robot_receiver`).
- Tests only: extend `test_watchdog.sh` / `test_named_pose.sh` (pose during timeout must not move; kill receiver → Servo halt).

No new architecture, no Dockerfile, no Jetson.

### Safety-B — WAN / Jetson (with M8 step 2, not before)

Goal: same nodes, hostile network, two clocks, two machines.

- `heartbeat_only` required on the robot.
- Software E-stop + Reset if a real arm is in the loop.
- Document that Zone 4 / E-stop hardware is the integrator’s job on Jetson.
- M9 monitor **reads** `/teleop/state`; it must not sit on the safety path (already in the plan).
- M10 measures watchdog trip time, loss, OOO — does not add safety logic.

### Explicitly not a milestone

Zone 2, perception SSM, force limiting, fleet, AI, energy isolation.

---

## Bottom line

The POC has a real **software protective stop for Cartesian jog** (heartbeat/watchdog → `/cmd_vel_safe`) and a **weak restart hold**. Session/seq/ack are **not** safety. MoveIt is a **motion limiter**, not a stop function. Named poses **bypass** the gateway. That is acceptable for local keyboard+Gazebo. It is not acceptable to copy unchanged onto WAN/Jetson without Safety-A (gate named pose, kill the bridge latch, heartbeat-only, hold after timeout).
