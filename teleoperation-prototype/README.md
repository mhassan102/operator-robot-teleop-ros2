# ROS 2 Teleoperation Prototype

This directory contains the incremental local ROS 2 Humble teleoperation proof
of concept. Milestone 7 drives a 6-DOF Gazebo arm with MoveIt Servo from the
Cartesian `/cmd_vel_safe` path, plus named-pose planning (`home`, `fold`,
`ready`, `observe`, `pregrasp`, `retract`, `stow`).

Product roadmap: [`../IMPLEMENTATION.md`](../IMPLEMENTATION.md).
Feature docs: [`docs/`](docs/) (`architecture.md`, `implementation-plan.md`,
`prototype.md`, keyboard steps in `teleop_gui_steps.txt`).

## Milestone 1 quick start

```bash
cp .env.example .env
./scripts/build.sh
./scripts/start.sh
docker compose exec operator ros2 --help
./scripts/stop.sh
```

`start.sh` performs the workspace build only when `ros2_ws/install` is absent
or the message package has not been built. After changing ROS source code,
rebuild it explicitly while the project is stopped:

```bash
./scripts/build_workspace.sh
```

## Transport and delivery tests

```bash
./scripts/start.sh
./scripts/test_basic.sh
./scripts/test_delivery.sh
./scripts/test_watchdog.sh
./scripts/test_console_session.sh
./scripts/test_console_named_pose.sh
./scripts/test_sim.sh
./scripts/test_named_pose.sh
```

Headless Gazebo is the default. For an interactive window (needs X11):

```bash
./scripts/start.sh --gui
```

Open the operator console at `http://127.0.0.1:8090/` (keys, heartbeat,
HUD, named-pose buttons, camera). F8 default camera is localhost
MediaMTX (`http://127.0.0.1:8889/cam`). TTY fallback:

```bash
./scripts/keyboard_teleop.sh
```

Focus that terminal and hold keys (`w/s` tool +x/x-, `a/d` +y/y-, `r/f` +z/z-,
`j/l` yaw, `g/h` gripper, space stop). That node streams the same
`/teleop/command` Twist as the CLI bursts, plus a heartbeat.

Publish a Cartesian jog burst:

```bash
docker compose exec operator \
  /teleop/entrypoint.sh ros2 run teleop_demo operator_command \
    --direction +x --count 5 \
    --ros-args --params-file /teleop/config/teleop.yaml
docker compose logs robot
```

`Twist` is a 6-DOF **tool jog** (m/s and rad/s), not a wheeled-base `cmd_vel`.
Directions: `+x` `x-` `+y` `y-` `+z` `z-` `+roll` `roll-` `+pitch` `pitch-`
`+yaw` `yaw-` `stop` `open` `close`. Negative axes are `x-` not `-x` so the
CLI does not treat them as flags. Aliases `forward`/`backward`/`left`/`right`
map to `+x`/`x-`/`+yaw`/`yaw-`. Gripper is a separate field (`0` closed, `1`
open). The safety node publishes validated output on `/cmd_vel_safe` and
`/gripper_safe`. MoveIt Servo turns that Twist into joint trajectories for a
Gazebo 6-DOF arm. A watchdog zeros the jog if the operator is silent for 500 ms.

Named poses are a separate robot-side service, not a `TeleopCommand` field:

```bash
./scripts/named_pose.sh fold
./scripts/named_pose.sh home
./scripts/named_pose.sh ready
./scripts/named_pose.sh observe
./scripts/named_pose.sh pregrasp
./scripts/named_pose.sh retract
./scripts/named_pose.sh stow
```

| Pose | Meaning |
| --- | --- |
| `home` | Known start |
| `fold` | Compact / safe idle |
| `ready` | Mid-reach, good pose to start jogging from |
| `observe` | Wrist up, looking at the table |
| `pregrasp` | Above a pick spot (open gripper with `g` if needed) |
| `retract` | Pull back after a grasp, still holding height |
| `stow` | Parked for shutdown / transport |

## F8 two-host (mlink Stage 5)

Operator Compose on this PC, robot Compose on Orin `nvidia-3`, mlink
between them. Apps talk UDP to `127.0.0.1`; they never see WAN IPs.
Copy-paste bring-up: [`../mlink-transport/docs/f8_usage.md`](../mlink-transport/docs/f8_usage.md).
Also [`../mlink-transport/docs/usage.md`](../mlink-transport/docs/usage.md)
(Stage 5). Scripts: `./scripts/start_operator_mlink.sh` (this PC),
`./scripts/start_robot_mlink.sh` (Orin), `./scripts/stop_mlink.sh`.

Robot container arm mode (`TELEOP_ARM`, default `gazebo`):

```bash
./scripts/start_robot_mlink.sh              # Gazebo + Servo + named poses (today)
./scripts/start_robot_mlink.sh --real-arm   # robot_receiver + Feetech gripper (+ mlink); no Gazebo
```

`--real-arm` / `TELEOP_ARM=real` must not fall back to Gazebo. Real mode
subscribes to `/gripper_safe` and commands the SO-ARM gripper servo only
(Feetech STS3215 id 6 on `/dev/ttyACM0`). `/dev/ttyACM0` is mounted only
when `TELEOP_ARM=real`. Localhost `./scripts/start.sh` is unchanged (Gazebo).

F18 Stage 3 (mlink over Tailscale to the SO-ARM laptop) is a separate
config pair: `mlink-transport/config/lab-op-remote-laptop.yaml` and
`lab-edge-remote-laptop.yaml`. Start with the existing daemon CLI
(`python3 -m op --config …` on this PC, `python3 -m edge --config …`
on the laptop). See
[`../mlink-transport/docs/usage.md`](../mlink-transport/docs/usage.md)
(F18 Stage 3). `start_operator_mlink.sh` / `start_robot_mlink.sh`
stay the Orin lab path. Do not start `--real-arm` for the mlink smoke.

The robot image on Orin is built there (aarch64). Gazebo Classic ROS
debs are not on arm64; `docker/Dockerfile` uses the Open Robotics
Gazebo 11 PPA and builds `gazebo_ros` / `gazebo_ros2_control` from
source on arm64 only. amd64 still uses `ros-humble-gazebo-ros-pkgs`.

Localhost tests below (`./scripts/start.sh` + `test_*.sh`) still use
**both containers on one host** via Zenoh on `ros2_teleop_poc_net`.
They do not exercise mlink. Keep them for the single-host path.

Default RMW is `rmw_zenoh_cpp`. The robot container runs `rmw_zenohd` on the
`ros2_teleop_poc_net` bridge; the operator connects as a client to
`tcp/robot:7447`. To fall back to CycloneDDS:

```bash
RMW_IMPLEMENTATION=rmw_cyclonedds_cpp ./scripts/start.sh
```

The Compose project uses the dedicated `ros2_teleop_poc_net` bridge and does not
modify unrelated containers. Only the operator container currently receives
`NET_ADMIN`; this will be used later to apply `tc netem` to that container's
egress without changing a host interface.
