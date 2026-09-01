# ROS 2 Teleoperation Prototype

This directory contains the incremental local ROS 2 Humble teleoperation proof
of concept. Milestone 4 adds a Cartesian arm command vocabulary, heartbeat,
and a watchdog safety gateway on top of the stamped DDS path from Milestone 3.

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
```

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
`/gripper_safe`. A watchdog zeros the jog if the operator is silent for 500 ms.

The Compose project uses the dedicated `ros2_teleop_poc_net` bridge and does not
modify unrelated containers. Only the operator container currently receives
`NET_ADMIN`; this will be used later to apply `tc netem` to that container's
egress without changing a host interface.
