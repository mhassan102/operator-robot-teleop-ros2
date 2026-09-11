# Zone 1 — Stopping & Restart: findings and implementation plan

Zone 1 is the six stop/restart functions from
[`robotics_safety_functions_25.txt`](robotics_safety_functions_25.txt).
This file is the Zone 1 follow-on to
[`coverage-analysis.md`](coverage-analysis.md). It is a plan only: no
code in this step. Product roadmap:
[`../IMPLEMENTATION.md`](../IMPLEMENTATION.md) (Feature F4 / F11).

**POC context.** Fixed-base 6-DOF Gazebo arm. Operator and robot are two Humble containers. Jog goes through `robot_receiver` → `/cmd_vel_safe` → `servo_bridge` → MoveIt Servo. Named poses go through `/teleop/go_named_pose` and **bypass** that gateway.

**Rules for this plan**

- Do not rewrite M0–M8 (command wire, Zenoh, Gazebo, keyboard keys).
- Do not invent a SIL / ISO 10218 product.
- Safety-A = local Docker harden. Safety-B = WAN/Jetson, not before M8 step 2.
- Hardware E-stop, STO, and drive standstill monitors stay out of this prototype.

**How the six functions are supposed to layer**

```text
E-Stop (latched, highest)          [not present]
  overrides
Protective Stop (watchdog / SAFE STOP)
  overrides
Normal Stop (space / zero jog / command timeout)
  then
Monitored Stationary (are joints actually still?)  [not present]
To move again:
  Reset  →  Start/Restart Interlock  →  CONNECTED jog or named pose
```

Today only Protective Stop (jog path) and a weak Normal Stop exist. Restart is too weak. Named pose ignores all of this.

---

## Status legend

| Mark | Meaning |
|---|---|
| **DONE** | In the POC now (M0–M7 + local Zenoh) |
| **TODO-A** | Safety-A, local Docker, same two containers |
| **TODO-B** | Safety-B, with WAN/Jetson (M8 step 2), not before |
| **OUT** | Not this prototype |

---

## 1. Normal Stop [MAND]

### Description

Operator (or the rate logic) asks the arm to stop jogging **without** treating the link as dead. Connection stays `CONNECTED`. `/cmd_vel_safe` goes to zero. This is “I let go of the key / I pressed space,” not “the operator vanished.”

### Use-case / example

Operator holds `w` to jog +x. They tap **space** (or release the key). The arm should stop in the Cartesian jog within ~200 ms, stay `CONNECTED`, and wait for the next key. Heartbeat keeps running. A later `w` should jog again with **no** Reset. If a named pose to `fold` is running, space should cancel that trajectory too — today it does not.

### Findings

Partial. Space and key-release publish a zero `TeleopCommand`. `command_timeout_ms: 200` zeros the safe twist while still `CONNECTED`. Servo can halt only if `servo_bridge` stops sending stamped twists. Named-pose motion is not cancelled. `servo_bridge` can keep a last non-zero latch until a zero arrives. Gripper is held.

### Implementation approach (this POC)

Keep the existing zero-twist path. Do not add a new stop topic for Normal Stop. Extend the **same** space/`stop` command so every motion writer honors it.

- **Node:** `keyboard_teleop` already sends `stop`. `robot_receiver` / `SafetyController` already zero on command timeout.
- **Topic:** `/teleop/command` with zero Twist; output `/cmd_vel_safe` = 0.
- **Add:** `named_pose` watches `/teleop/state` (or a stop flag on the command) and cancels `move_action` if `last_disposition` is `ZEROED` while a pose is running, **or** subscribe to zero-twist `/cmd_vel_safe` / a small `/teleop/stop` only if we need a pose-cancel without overloading Twist. Prefer: `named_pose` cancels when it sees `safe_twist` all zeros **and** a stop key was the cause is hard to see — cleaner **TODO-A**: `named_pose` also subscribes to `/teleop/command`; if Twist is zero and a pose is busy, cancel the goal. That still uses the existing command topic (no M0–M8 rewrite).
- **Add:** `servo_bridge` must apply zero immediately (it already copies `/cmd_vel_safe`; the hole is stale latch when the **receiver** dies — that is Protective Stop, function 2).
- Local Docker first. No WAN-specific change.

### Steps

| Step | Status | What |
|---|---|---|
| Space / `stop` publishes zero Twist on `/teleop/command` | **DONE** | `keyboard_teleop.py` key ` `; `commands.py` `stop` |
| Key release zeros jog after 0.3 s | **DONE** | `keyboard_teleop._current` |
| 200 ms command silence zeros `/cmd_vel_safe` without `TIMEOUT` | **DONE** | `safety.py` `command_timeout_s`; `teleop.yaml` `command_timeout_ms: 200` |
| Unit test: command silence zeros jog, still `CONNECTED` | **DONE** | `test_safety.py` `test_command_silence_zeros_jog_without_timeout` |
| Zero `/cmd_vel_safe` reaches Servo as zero TwistStamped | **DONE** | `servo_bridge` copies last twist; zero is copied when receiver publishes it |
| Space / zero Twist cancels an in-flight named pose | **TODO-A** | `named_pose` subscribe `/teleop/command` (or `/teleop/state`); `move_action` cancel; then `start_servo` |
| After cancel, Servo must not resume an old jog | **TODO-A** | Same as function 2 latch: bridge must be holding zero |
| Gripper policy on Normal Stop (hold vs close) | **TODO-A** | Keep **hold** unless we document otherwise; do not slam fingers |
| Hardware Category 1/2 stop, deceleration monitor | **OUT** | Drive/PLC, not this stack |

---

## 2. Protective Stop [MAND]

### Description

The robot decides the operator link (or safety node) is no longer valid and **forces** a stop. This is not the operator choosing to stop. Output must go to zero, last jog must not replay, and **every** motion writer must refuse new motion until Reset / restart interlock.

### Use-case / example

Operator is jogging +x over the keyboard. They close the keyboard terminal (or the WAN drops). Within **500 ms** the robot logs `TIMEOUT` and `SAFE STOP ACTIVATED`, `/cmd_vel_safe` is 0, and a `named_pose.sh fold` called during that window is **rejected**. If `robot_receiver` itself crashes while the last twist was +x, `servo_bridge` must still halt — today it would keep stamping the last +x.

### Findings

Partial on the jog path only. 500 ms watchdog, startup `SAFE STOP`, no stale replay after heartbeat restore (when heartbeat restores first). Default keep-alive is `command_or_heartbeat` (a stuck command publisher looks alive). Named pose ignores this state. `servo_bridge` latches last `/cmd_vel_safe` with **fresh stamps**, so Servo `incoming_command_timeout: 0.2` does not cover receiver death.

### Implementation approach (this POC)

Keep `SafetyController` as the single protective-stop brain. Do not add a second watchdog. Close the two bypasses: named pose and bridge latch. Switch WAN default to `heartbeat_only`.

- **Node:** `robot_receiver` + `safety.py` (already). `named_pose` must **read** `/teleop/state`. `servo_bridge` must **time out** `/cmd_vel_safe`.
- **Topics:** `/teleop/heartbeat`, `/teleop/command` (keep-alive today), `/teleop/state` (`watchdog_state`, `connection_state`), `/cmd_vel_safe`.
- **TODO-A local:**
  1. `named_pose._on_request`: if `connection_state == TIMEOUT` or `watchdog_state != OK`, return `success=false` (`"protective stop active"`). If a pose is running when state becomes `TIMEOUT`, cancel `move_action`.
  2. `servo_bridge`: if no `/cmd_vel_safe` for ~200–500 ms, set internal twist to zero (do not keep last value). Then Servo either gets zeros or, if the bridge also dies, `incoming_command_timeout` halts.
  3. Tests: pose during timeout must not move; `docker compose exec robot pkill -f robot_receiver` while jogging → tool pose holds.
- **TODO-B WAN:** `watchdog_keep_alive: heartbeat_only` on the robot. Keyboard already publishes heartbeat. Scripted `operator_command` tests keep the old param locally if needed.
- Hardware STO / safety I/O: **OUT**.

### Steps

| Step | Status | What |
|---|---|---|
| Startup latches `SAFE STOP ACTIVATED`, twist 0 | **DONE** | `safety.py` `_pending_startup_stop`; `test_watchdog.sh` |
| 500 ms no keep-alive → `TIMEOUT` + `SAFE STOP` + zero `/cmd_vel_safe` | **DONE** | `watchdog_timeout_ms: 500`; `_safety_tick` |
| Last non-zero twist discarded on timeout (no replay after heartbeat restore) | **DONE** | `test_watchdog.sh`; `test_safety.py` `test_watchdog_timeout_zeros_and_does_not_replay` |
| `/teleop/state` publishes connection + watchdog | **DONE** | `robot_receiver._publish_state` |
| `heartbeat_only` policy exists and is unit-tested | **DONE** | `KEEP_ALIVE_HEARTBEAT_ONLY`; not the **running** default |
| Running default `command_or_heartbeat` | **DONE** (local demo) / **TODO-B** to change for WAN | `teleop.yaml` |
| Named pose refused (and cancelled) during `SAFE STOP` / `TIMEOUT` | **TODO-A** | `named_pose` + `/teleop/state` |
| `servo_bridge` zeros if `/cmd_vel_safe` silent | **TODO-A** | Timer in `servo_bridge._tick` |
| Receiver process death does not leave Servo jogging | **TODO-A** | Relies on bridge timeout above; prove with kill test |
| Bind heartbeat `session_id` to command session | **TODO-A** (nice) / **TODO-B** (WAN) | Ignore foreign heartbeats |
| `heartbeat_only` default on robot for WAN/Jetson | **TODO-B** | Same node, param only |
| Hardware protective stop / STO | **OUT** | Integrator on real arm |

---

## 3. E-Stop [COND]

### Description

A **latched** emergency stop, independent of heartbeat and of “I released the key.” Until Reset, no jog and no named pose. In a product this is a dual-channel mushroom and STO. In this POC it can only be a software latch.

### Use-case / example

Operator sees the Gazebo arm heading at a mesh they did not mean to hit. They hit **E** (or a future GUI red button). The arm stops even if the keyboard is still repeating `w` and heartbeats are still flowing. Releasing E does **not** start motion. They must Reset, then send a new jog. Closing the keyboard without pressing E is Protective Stop (function 2), not E-Stop.

### Findings

Not present. Space is Normal Stop. Watchdog is Protective Stop. Neither latches against a live command stream.

### Implementation approach (this POC)

Software-only, **after** Protective Stop covers all writers (function 2 TODO-A). Do not put this on the critical path of the local keyboard demo.

- **Node:** extend `SafetyController` with `estop_latched: bool`. `robot_receiver` subscribes. Do **not** create a separate safety stack.
- **Interface (suggested):** `std_msgs/Bool` `/teleop/estop` (`true` = engage) **or** a latching service `Trigger /teleop/estop`. Prefer a **topic** so a dead operator cannot “unpress” by disappearing — actually disappearing should stay Protective Stop. E-Stop engage can be a topic; **clear only via Reset** (function 5).
- **Keyboard:** one key (e.g. `e`) publishes `true` once. Do not auto-clear on key release.
- **Effect:** same outputs as Protective Stop (zero `/cmd_vel_safe`, reject named pose) plus ignore keep-alive until Reset. `connection_state` can stay a new string e.g. `ESTOP` or reuse `SAFE STOP` with `last_disposition` / a new `watchdog_state` field. Prefer a dedicated `estop_state` on `TeleopState` so M9 can show it — that is a small msg field, not a new architecture.
- Hardware mushroom / dual channel: **OUT**. On Jetson, document that the real button must cut motor power, not only publish ROS.

### Steps

| Step | Status | What |
|---|---|---|
| Software E-Stop topic/service + latch in `SafetyController` | **TODO-B** | After function 2 writers are gated |
| Keyboard (or GUI) engage; does not self-clear | **TODO-B** | `keyboard_teleop` extra key |
| Named pose + jog both blocked while latched | **TODO-B** | Same gate as function 2 |
| Clear only through Reset (function 5) | **TODO-B** | See function 5 |
| Test: heartbeats + `w` still produce zero twist during E-Stop | **TODO-B** | Extend `test_watchdog.sh` or a small `test_estop.sh` |
| Hardware E-stop, Category 0/1, dual-channel | **OUT** | Not this prototype |

---

## 4. Start/Restart Interlock [MAND]

### Description

After a protective stop (or E-Stop), motion must not resume from the same packet that “proves liveness.” Sequence is: link is back → operator is aware (`RESTORED` / Reset) → **then** a new command may move. First connection at process start may go `CONNECTED` on the first valid keep-alive; after a fault it must not.

### Use-case / example

Watchdog trips (`TIMEOUT`). Heartbeat returns. `/cmd_vel_safe` stays 0 (`RESTORED`). Operator then holds `w`. Arm jogs. **Bad case today:** with `command_or_heartbeat`, a delayed `+x` that arrives after timeout both restores **and** moves in one callback — the arm jumps as the link comes back. That must not happen on WAN.

### Findings

Partial. Startup is stopped. Heartbeat-first restore holds until a later command (`test_watchdog.sh`). A command in `TIMEOUT` with default keep-alive calls `_register_keep_alive` → `RESTORED` → then applies that same Twist. No check that named pose is idle. No enable device.

### Implementation approach (this POC)

Fix the state machine in `safety.py` only. No new node.

Change `on_command` so keep-alive that leaves `TIMEOUT` **never** applies that command:

1. Register keep-alive (may set `RESTORED`).
2. If previous state was `TIMEOUT` **or** current is `RESTORED` and `_awaiting_fresh_command`, return `HELD` and do not copy twist.
3. Next command after the node is already `RESTORED`/`CONNECTED` with `_awaiting_fresh_command` can be the enable — **or** require a dedicated enable (function 5) before `_awaiting_fresh_command` clears.

**Safety-A (minimum):** step 1–2 so a restoring command is `HELD`. The **following** command may move (two packets, still no extra UI).

**Safety-B:** `_awaiting_fresh_command` clears only after Reset (function 5), then a jog. `heartbeat_only` so a command cannot restore at all.

Named pose: only allowed when `connection_state == CONNECTED` and not `_awaiting_fresh_command` (exposed via `/teleop/state`).

### Steps

| Step | Status | What |
|---|---|---|
| First keep-alive from cold start → `CONNECTED` | **DONE** | `_ever_connected` false path |
| Heartbeat after timeout → `RESTORED`, twist stays 0 | **DONE** | `test_watchdog.sh` heartbeat section |
| Fresh command **after** heartbeat restore → `CONNECTED` and moves | **DONE** | Same test |
| Command that itself restores is `HELD`, does not move | **TODO-A** | `safety.py` `on_command` order; new unit test |
| Named pose blocked until `CONNECTED` (not `RESTORED`) | **TODO-A** | Same `/teleop/state` gate as function 2 |
| WAN: only heartbeat restores; command never keep-alives | **TODO-B** | `heartbeat_only` |
| WAN: Reset required before the enabling jog | **TODO-B** | Function 5 |
| Physical enable device / three-position switch | **OUT** | |

---

## 5. Reset [COND]

### Description

An explicit operator action that clears a latched fault (Protective Stop after timeout, or E-Stop) and allows the Start/Restart Interlock to arm. Reset must **not** move the arm. Reset is not the same as “heartbeat came back.”

### Use-case / example

After a WAN glitch the log shows `TIMEOUT` then `RESTORED`. The arm is still. Operator hits **Reset** (keyboard `n` or `ros2 service call /teleop/reset`). State becomes ready-to-enable (`CONNECTED` or a `RESET_OK` that still needs a jog). Only then does `w` move. If E-Stop was latched, Reset is the only clear.

### Findings

Partial analogue: automatic `RESTORED` on keep-alive. No operator Reset. No distinction between “link is back” and “I acknowledge the stop.”

### Implementation approach (this POC)

Not needed for the local keyboard demo if function 4 TODO-A is done (held restoring command). Add for WAN / E-Stop.

- **Node:** `robot_receiver` service `Trigger /teleop/reset` (or `std_srvs/Trigger`). `SafetyController.reset()`: if keep-alive is fresh and E-Stop is clearable, set `RESTORED` → allow next command to connect; **never** copy a stored twist.
- **Refuse Reset** if heartbeat is not currently alive (`heartbeat_only` WAN) — otherwise Reset would arm a dead link.
- **Keyboard:** optional key that calls the service. M9 monitor can show “Reset required.”
- Local: skip until E-Stop exists, unless we want the service early as a no-op that logs.

### Steps

| Step | Status | What |
|---|---|---|
| Automatic `RESTORED` on keep-alive after timeout | **DONE** | Not a true Reset |
| Explicit `/teleop/reset` that does not command motion | **TODO-B** | `Trigger` on `robot_receiver` |
| Reset refused unless heartbeat currently OK | **TODO-B** | Prevents arming a dead WAN |
| Reset is the only way to clear software E-Stop | **TODO-B** | Function 3 |
| Keyboard / monitor Reset action | **TODO-B** | M9 may display; must not sit on safety path |
| Safety-rated reset button hardware | **OUT** | |

---

## 6. Monitored Stationary Position [COND]

### Description

After a stop, **verify** the arm is actually still (joint velocity / position hold), not only that we commanded zero Twist. Product robots do this in the drive (SS1/SS2, standstill monitor). Gazebo can only approximate it.

### Use-case / example

Protective Stop fires. `/cmd_vel_safe` is 0, but a named-pose trajectory is still playing or Gazebo overshoots. Within a short window, `/joint_states` velocities should fall below a threshold (e.g. 0.05 rad/s, already used as `stopped_velocity_tolerance`). If not, log `STATIONARY FAULT` and refuse Reset / new jog until it is true (or until a timeout then fault). Example: kill Servo’s halt but leave `arm_controller` executing `fold` — monitor must still see motion and refuse “we are stopped.”

### Findings

Not present as a safety function. `arm_controllers.yaml` `stopped_velocity_tolerance: 0.05` is a **trajectory goal** check, not a continuous monitor. Commanded zero ≠ measured standstill.

### Implementation approach (this POC)

Optional, lightweight, **not** a certified standstill. Only if we want a demo-grade “we looked at joints.”

- **Node:** small timer in `robot_receiver` **or** `servo_bridge` (receiver already owns `/teleop/state`). Subscribe `/joint_states`.
- **Logic:** when `watchdog_state == SAFE STOP` or Normal Stop zeros output, require `max(|dq|) < threshold` for N consecutive cycles. Publish `stationary: true/false` on `/teleop/state` (new field) or log only.
- **Do not** block the local demo on this. **Do not** pretend this is ISO standstill.
- Real arm / Jetson: **OUT** of ROS — use the driver/STO.

### Steps

| Step | Status | What |
|---|---|---|
| Commanded zero twist on stop | **DONE** | Functions 1–2 |
| JTC `stopped_velocity_tolerance` on named-pose **goal** | **DONE** | Not a monitor |
| Subscribe `/joint_states`, compare velocity while in `SAFE STOP` | **TODO-A** (optional) | `robot_receiver` timer; log + state field |
| Refuse Reset / named pose if not stationary | **TODO-A** (optional) | Ties to functions 4–5 |
| Test: during `fold`, monitor reports not stationary | **TODO-A** (optional) | |
| Drive-level SS1/SS2 / encoder standstill | **OUT** | Hardware |

---

## Zone 1 implementation order (do not start code in this document)

Do these in order so we do not rewrite M0–M8 or jump to WAN.

### Safety-A — local Docker (same two containers)

1. **Protective Stop covers named pose** — refuse/cancel `/teleop/go_named_pose` on `TIMEOUT` / `SAFE STOP`.
2. **`servo_bridge` stale `/cmd_vel_safe` → zero** — receiver death cannot keep jogging.
3. **Restart hold** — restoring command is `HELD`; next command may move.
4. **Normal Stop cancels named pose** — space stops planned motion too.
5. **Optional:** joint-velocity “stationary” flag on `/teleop/state`.

Prove with: existing `test_watchdog.sh`, plus pose-during-timeout, plus kill-`robot_receiver`.

### Safety-B — with M8 step 2 (WAN / Jetson), not before

6. `heartbeat_only` on the robot.
7. Heartbeat `session_id` must match command session.
8. Software E-Stop latch + `/teleop/reset`.
9. Reset required after timeout/E-Stop before jog.

### Never in this prototype

Hardware E-Stop, STO, dual-channel, Category 0/1, certified standstill, enable-grip.

---

## Touch map (existing nodes only)

| Function | Primary node | Topics / services | New node? |
|---|---|---|---|
| 1 Normal Stop | `keyboard_teleop`, `safety.py`, `named_pose` | `/teleop/command`, `/cmd_vel_safe` | No |
| 2 Protective Stop | `robot_receiver`, `servo_bridge`, `named_pose` | `/teleop/heartbeat`, `/teleop/state`, `/cmd_vel_safe` | No |
| 3 E-Stop | `robot_receiver` + keyboard | `/teleop/estop` (new), `/teleop/state` | No |
| 4 Start/Restart Interlock | `safety.py` | same state machine | No |
| 5 Reset | `robot_receiver` | `/teleop/reset` (new service) | No |
| 6 Monitored Stationary | `robot_receiver` (optional) | `/joint_states`, `/teleop/state` | No |

All six stay inside the current receiver / bridge / named-pose / keyboard set. No Dockerfile change, no Gazebo rewrite, no M8 step 2 in Safety-A.

---

## Zone 1 coverage snapshot

| # | Function | Tag | Now | Next |
|---|---|---|---|---|
| 1 | Normal Stop | MAND | Partial (jog only) | TODO-A: cancel named pose |
| 2 | Protective Stop | MAND | Partial (jog only) | TODO-A: named pose + bridge latch; TODO-B: `heartbeat_only` |
| 3 | E-Stop | COND | Not present | TODO-B software latch; hardware OUT |
| 4 | Start/Restart Interlock | MAND | Partial | TODO-A: hold restoring command |
| 5 | Reset | COND | Automatic `RESTORED` only | TODO-B explicit Reset |
| 6 | Monitored Stationary Position | COND | Not present | Optional TODO-A log; drive monitor OUT |
