# Implementation Plan: ROS 2 Remote Teleoperation Prototype

Read this file first in a new session. Each milestone has:

- `status`: `done` or `remaining` (code exists and was verified)
- `committed`: `done` or `remaining` (on `main`)

## 0. Where we stand (2026-09-02)

**First target (not finished):** keyboard teleoperation from this Ubuntu machine
over a **WAN**, using **rmw_zenoh**, to an **NVIDIA Jetson** that runs either
Gazebo or a hardware 6-DOF arm. Camera/video is out of scope for that target.

**Already working locally (Docker, one host, CycloneDDS):**

```text
keyboard / scripted CLI
  -> operator container
  -> TeleopCommand (Twist + seq + stamp + session + gripper)
  -> CycloneDDS on ros2_teleop_poc_net
  -> robot safety + 500 ms watchdog
  -> /cmd_vel_safe
  -> MoveIt Servo
  -> Gazebo Classic 6-DOF arm
```

Quick GUI:

```text
cd teleoperation-prototype
./scripts/start.sh --gui
./scripts/keyboard_teleop.sh
```

See `teleoperation-prototype/teleop_gui_steps.txt`.

| Milestone | Title | status | committed | git |
|---|---|---|---|---|
| 0 | Host baseline / isolate Compose | done | done | `e4a9bde` |
| 1 | Humble containers, CycloneDDS | done | done | `e4a9bde` |
| 2 | Cross-container command transport | done | done | `91f26ab` |
| 3 | Stamped commands, ack, latency | done | done | `60edc97` |
| 4 | Cartesian Twist, heartbeat, watchdog | done | done | `0faba42` |
| 5 | Gazebo 6-DOF arm from `/cmd_vel_safe` | done | done | `4ba8c4e` |
| 6 | Keyboard teleop | done | done | `fe647cd` |
| 7 | MoveIt Servo / planning | done | remaining | — |
| 8 | Replace CycloneDDS with Zenoh | remaining | remaining | — |
| 9 | Operator monitor / telemetry | remaining | remaining | — |
| 10 | Benchmarking (local and WAN) | remaining | remaining | — |

Dropped from the original plan (not needed for the first WAN/Jetson target):

- container `tc netem` impairment profiles
- DDS/RTPS packet capture
- simulated camera / video path
- wheeled / differential-drive robot

## 1. Purpose

Implement the prototype in
`task1_ros_local_remote_teleoperation_protoype.md` as gated milestones.

The **local** POC is a 6-DOF arm (not a mobile base). Operator and robot are
separate Humble containers. Commands are Cartesian tool jogs (`geometry_msgs/Twist`
inside `TeleopCommand`), not wheel `cmd_vel`.

The **next** POC is the same keyboard path across machines with Zenoh.

## 2. Constraints

- Host: Ubuntu 22.04, Docker Engine 28, Compose v2.
- ROS and Gazebo live in images, not on the host.
- Compose project `ros2_teleop_poc`; do not touch unrelated containers.
- Safety gateway is the only path to the simulator/motors.
- Local one-way latency is valid (shared host clock). WAN will use RTT / Zenoh
  timestamps as needed.
- Jetson + hardware arm come after Zenoh works between two hosts.

## 3. Current architecture

```text
Host keyboard + Gazebo GUI
        |
        v
+------------- operator container --------------+
| keyboard_teleop / operator_command            |
|   /teleop/command   TeleopCommand             |
|   /teleop/heartbeat TeleopHeartbeat           |
+-----------------------+-----------------------+
                        | ROS 2 Humble
                        | today: CycloneDDS (rmw_cyclonedds_cpp)
                        | next:  rmw_zenoh (M8)
                        v
+------------- robot container -----------------+
| robot_receiver: ack, stats, safety, watchdog  |
|   /cmd_vel_safe  Twist                        |
|   /gripper_safe  Float64                      |
| MoveIt Servo + named poses (home / fold)      |
| Gazebo Classic 6-DOF arm + gripper            |
|   /teleop/tool_pose                           |
+-----------------------------------------------+
```

```text
/teleop/command -> validate/watchdog -> /cmd_vel_safe -> Servo -> Gazebo joints
```

## 4. Repository layout (as built)

```text
teleoperation-prototype/
├── compose.yaml
├── config/{cyclonedds.xml,teleop.yaml}
├── docker/{Dockerfile,entrypoint.sh}
├── docs/{architecture.md,networking.md,testing.md}
├── teleop_gui_steps.txt
├── ros2_ws/src/
│   ├── teleop_demo/          # nodes, launch, urdf, tests
│   └── teleop_demo_msgs/     # TeleopCommand, Ack, Heartbeat, State
└── scripts/
    ├── build.sh, build_workspace.sh, start.sh, stop.sh
    ├── keyboard_teleop.sh
    ├── test_basic.sh, test_delivery.sh, test_watchdog.sh, test_sim.sh
    └── shell_operator.sh, shell_robot.sh, preflight.sh
```

## 5. Milestones

### Milestone 0: Record baseline and protect the host

- status: **done**
- committed: **done** (`e4a9bde`)

Preflight, unique Compose names, no host `tc` changes, no unrelated container
edits.

### Milestone 1: Scaffold the containerized ROS 2 workspace

- status: **done**
- committed: **done** (`e4a9bde`)

Humble image, operator + robot, CycloneDDS, domain 42, dedicated bridge
`ros2_teleop_poc_net`.

### Milestone 2: Prove ROS 2 discovery and command transport

- status: **done**
- committed: **done** (`91f26ab`)

Scripted publisher and robot receiver. Originally `/cmd_vel_raw` Twist; later
milestones replaced that topic with `/teleop/command`.

### Milestone 3: Command metadata and measurable delivery

- status: **done**
- committed: **done** (`60edc97`)

`TeleopCommand` / `TeleopAck`: sequence, timestamps, session, latency/RTT,
loss/duplicate/out-of-order counters. Independent of wheel vs arm.

### Milestone 4: Safety gateway, heartbeat, watchdog, Cartesian Twist

- status: **done**
- committed: **done** (`0faba42`)

6-axis tool Twist + `frame_id` + gripper. `/cmd_vel_safe`, `/gripper_safe`,
`/teleop/state`. 500 ms watchdog. States: `CONNECTED`, `TIMEOUT`,
`SAFE STOP ACTIVATED`, `RESTORED`.

### Milestone 5: 6-DOF arm in Gazebo

- status: **done**
- committed: **done** (`4ba8c4e`)

Gazebo Classic, custom arm URDF, `ros2_control`, Jacobian jogger from
`/cmd_vel_safe`. Headless tests (`test_sim.sh`) and GUI (`start.sh --gui`).
Not a differential-drive robot.

### Milestone 6: Keyboard teleop

- status: **done**
- committed: **done** (`fe647cd`)

Node `keyboard_teleop` in the operator container streams `/teleop/command` and
`/teleop/heartbeat`. Wrapper: `./scripts/keyboard_teleop.sh` (needs `-it` TTY).

Keys: `w/s` +x/x-, `a/d` +y/y-, `r/f` +z/z-, `j/l` yaw, `u/o` roll, `i/k`
pitch, `g/h` gripper, space stop. Hold to jog; release zeros after 0.3 s.

CLI `operator_command` remains for automated tests.

### Milestone 7: MoveIt Servo / planning

- status: **done** (verified locally; not committed)
- committed: **remaining**

Replaced the homemade Jacobian jogger with **MoveIt Servo** for Cartesian
streaming (`/cmd_vel_safe` Twist → `servo_bridge` TwistStamped → Servo →
`arm_controller` JointTrajectory). Added **MoveIt planning** for named poses
(`home` / `fold`) via `/teleop/go_named_pose` and `./scripts/named_pose.sh`.

Operator wire (`TeleopCommand`, keyboard keys) unchanged. Safety still sits
in front of Servo.

### Milestone 8: Replace CycloneDDS with Zenoh

- status: **remaining**
- committed: **remaining**

Switch RMW from `rmw_cyclonedds_cpp` to **`rmw_zenoh_cpp`** (or equivalent
Humble Zenoh RMW).

1. Same two containers on one host, prove keyboard jog still works.
2. Split operator (this machine) and robot (NVIDIA Jetson) across a WAN.
3. Robot side: Jetson runs Humble + either Gazebo or the hardware arm driver
   behind the same safety node.

This is the transport for the first remote-teleop target. Do not add a parallel
DDS capture milestone.

### Milestone 9: Operator monitor / telemetry

- status: **remaining**
- committed: **remaining**

Optional but useful for WAN: terminal (or line) monitor of connection,
watchdog, tool pose (`/teleop/tool_pose`), command age, latency, loss.
Must not sit on the safety path. Camera is out of scope.

### Milestone 10: Benchmarking

- status: **remaining**
- committed: **remaining**

Measure, do not emulate with `netem`:

- local Docker baseline (already ~1–2 ms one-way on CycloneDDS)
- Zenoh on one host vs two hosts / WAN
- command rate, one-way or RTT, loss, out-of-order, watchdog trip time
- CPU on host and Jetson while jogging

Scripts with clear numbers and logs. No video bandwidth work in this milestone.

## 6. ROS 2 interfaces (current)

| Topic | Direction | Type |
|---|---|---|
| `/teleop/command` | operator → robot | `teleop_demo_msgs/TeleopCommand` |
| `/teleop/heartbeat` | operator → robot | `teleop_demo_msgs/TeleopHeartbeat` |
| `/teleop/ack` | robot → operator | `teleop_demo_msgs/TeleopAck` |
| `/teleop/state` | robot → operator | `teleop_demo_msgs/TeleopState` |
| `/cmd_vel_safe` | safety → servo_bridge | `geometry_msgs/Twist` (tool rate) |
| `/gripper_safe` | safety → servo_bridge | `std_msgs/Float64` |
| `/teleop/tool_pose` | servo_bridge → all | `geometry_msgs/PoseStamped` |
| `/teleop/go_named_pose` | operator → robot | `teleop_demo_msgs/srv/GoNamedPose` |
| `/joint_states` | Gazebo → all | `sensor_msgs/JointState` |

`/odom` and `/camera/image_raw` are not part of the first target.

## 7. Tests (existing)

- `./scripts/test_basic.sh` — discovery, Cartesian directions, burst, domain
- `./scripts/test_delivery.sh` — 20 Hz / 30 s, loss counters, session reset
- `./scripts/test_watchdog.sh` — safe stop, no stale replay
- `./scripts/test_sim.sh` — Gazebo tool pose moves and holds
- `./scripts/test_named_pose.sh` — fold/home planning, then jog still works

## 8. Definition of done (first target)

```text
keyboard on this Ubuntu host
  -> operator
  -> ROS 2 / Zenoh over WAN
  -> Jetson (Gazebo or hardware arm)
  -> safety + watchdog
  -> MoveIt Servo
  -> arm motion
  -> pose/ack back
  -> measured latency (M10)
```

Local keyboard + Gazebo + Servo (M0–M7) is done. Remaining: M8 Zenoh/Jetson WAN,
M9 monitor, M10 benchmarks.
