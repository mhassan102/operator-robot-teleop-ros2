# Task: ROS 2 Remote Teleoperation Prototype (6-DOF arm)

**Living status:** see
[`implementation-plan.md`](implementation-plan.md)
(milestones with `status` / `committed`). Product roadmap:
[`../../IMPLEMENTATION.md`](../../IMPLEMENTATION.md). This file is the
original teleop product intent.
The original wheeled-robot + netem + camera wording below is **superseded**
where it conflicts with the first target.

## First target (current)

Do **remote keyboard teleoperation** from this Ubuntu machine over a **WAN**,
using **Zenoh** (`rmw_zenoh`), to an **NVIDIA Jetson** that runs:

- the same robot-side safety stack, and
- either **Gazebo** (6-DOF arm) or a **hardware arm**.

Already done locally (Docker, CycloneDDS, one host): containers, stamped
Cartesian commands, watchdog, Gazebo arm motion, keyboard teleop.

Out of scope for this first target:

- simulated camera / video
- `tc netem` WAN emulation (use the real WAN + benchmarks)
- DDS/RTPS capture (transport becomes Zenoh)
- TurtleBot / differential-drive

## Environment

Host OS: Ubuntu 22.04

Use:

- Docker / Docker Compose on the operator machine
- ROS 2 Humble
- Gazebo Classic for the local/Jetson simulator
- 6-DOF arm (custom Gazebo model now; hardware later)
- Keyboard teleop (implemented)
- CycloneDDS today; **Zenoh** for the WAN split
- Jetson as the robot-side computer

## Target architecture

```text
        This machine (operator)              NVIDIA Jetson (robot)
     ┌─────────────────────┐              ┌──────────────────────────┐
     │ ROS 2 Humble        │              │ ROS 2 Humble             │
     │ Keyboard teleop     │   Zenoh      │ Safety + watchdog        │
     │ (optional monitor)  │─────────────▶│ MoveIt Servo (planned)   │
     └─────────────────────┘              │ Gazebo arm or hardware   │
                                          └──────────────────────────┘
```

Local Docker still uses two containers (operator + robot) as the development
stand-in for those two machines.

## Control path (as built)

```text
Keyboard / scripted CLI
   ↓
TeleopCommand (Twist 6-axis tool jog + seq + stamp + session + gripper)
   ↓
ROS 2 (CycloneDDS now, Zenoh next)
   ↓
Safety / 500 ms watchdog
   ↓
/cmd_vel_safe
   ↓
MoveIt Servo
   ↓
Gazebo 6-DOF arm  (or hardware on Jetson)
```

`Twist` is a **tool rate**, not wheel `cmd_vel`. Named poses (`home` / `fold`)
are MoveIt goals via `./scripts/named_pose.sh`, not reverse jogs.

## Keyboard (done)

```text
./scripts/start.sh --gui
./scripts/keyboard_teleop.sh
```

`w/s` +x/x-, `a/d` +y/y-, `r/f` +z/z-, `j/l` yaw, `u/o` roll, `i/k` pitch,
`g/h` gripper, space stop. See [`teleop_gui_steps.txt`](teleop_gui_steps.txt).

## Safety (done)

Heartbeat + command keep-alive. If silent > 500 ms: zero jog, log
`TIMEOUT` / `SAFE STOP ACTIVATED`. Fresh command after restore; do not replay
stale Twist.

## Remaining product work

Tracked in [`../../IMPLEMENTATION.md`](../../IMPLEMENTATION.md), not
here:

1. **MoveIt Servo / planning** — done (in this tree).
2. **Operator web console (F5)** — remaining; next product feature.
3. **WAN** — mlink Stage 5 (F8) + Jetson split (F12), not Zenoh-across-the-internet.
4. **Monitor** — console telemetry (F5.3).
5. **Benchmarking** — F19.

## Original task notes (historical)

The rest of this document was the first write-up (mobile robot, netem, DDS
inspect, video). Treat it as background. Do **not** implement TurtleBot,
`network_*.sh`, tcpdump DDS, or camera unless the first target above is done
and a later phase explicitly asks for them.

---

# Original request (kept for history)

I want to build a small end-to-end remote teleoperation prototype on my Ubuntu 22.04 development machine.

The purpose of this prototype is not to build a production robot system yet. The goal is to create a practical learning and testing environment where I can understand how remote teleoperation works, including ROS 2 communication, simulated robot control, video/telemetry flow, network latency, packet loss, watchdog behavior, and DDS networking.

Please inspect the current workspace first, then create the required project structure, configuration files, Dockerfiles, scripts, and documentation.

## Environment

Host OS:

Ubuntu 22.04

Use:

- Docker / Docker Compose
- ROS 2 Humble
- Gazebo or another ROS 2-compatible lightweight robot simulator
- CycloneDDS if practical
- Python or C++ ROS 2 nodes
- Linux networking tools such as tc/netem
- tcpdump/Wireshark-compatible traffic
- Bash scripts for startup/testing

Keep the prototype fully local initially. No cloud service or external API should be required.

## Target Architecture

Create two logical sides:

```text
                Ubuntu 22.04 Host

     Operator Side                Robot Side
   ┌─────────────────┐        ┌────────────────────┐
   │ ROS 2           │        │ ROS 2              │
   │ Keyboard Teleop │        │ Robot Controller   │
   │ Status Viewer   │        │ Gazebo Simulator   │
   │ Video Viewer    │        │ Camera/Sensors     │
   └────────┬────────┘        └─────────┬──────────┘
            │                           │
            │       ROS 2 / DDS         │
            └────────── Network ─────────┘
```

Prefer separate Docker containers for the operator and robot sides.

## Phases (mapped to the living plan)

- Phase 1 basic ROS 2: **done** (M1–M2)
- Phase 2 simulated robot: **done as 6-DOF Gazebo arm**, not TurtleBot (M5)
- Phase 3 telemetry: **remaining** as M9 monitor
- Phase 4 heartbeat/watchdog: **done** (M4)
- Phase 5 netem: **dropped** (real WAN + M10 benchmarks)
- Phase 6 latency: **done locally** (M3); WAN numbers in M10
- Phase 7 DDS inspect: **dropped** (M8 is Zenoh)
- Phase 8 video: **dropped** for first target
- Phase 9 monitoring: **remaining** M9
- Keyboard: **done** (M6)

Safety, YAML config, and incremental build still apply as originally requested.
