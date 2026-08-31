# ROS 2 Teleoperation Prototype

This directory contains the incremental local ROS 2 Humble teleoperation proof
of concept. Milestone 2 provides verified CycloneDDS command transport between
the operator and robot containers.

## Milestone 1 quick start

```bash
cp .env.example .env
./scripts/build.sh
./scripts/start.sh
docker compose exec operator ros2 --help
./scripts/stop.sh
```

`start.sh` performs the workspace build only when `ros2_ws/install` is absent.
After changing ROS source code, rebuild it explicitly while the project is
stopped:

```bash
./scripts/build_workspace.sh
```

## Milestone 2 transport test

```bash
./scripts/start.sh
./scripts/test_basic.sh
```

Publish one command manually:

```bash
docker compose exec operator \
  /teleop/entrypoint.sh ros2 run teleop_demo operator_command \
    --direction forward --count 5
docker compose logs robot
```

Valid directions are `forward`, `backward`, `left`, `right`, and `stop`.

The Compose project uses the dedicated `ros2_teleop_poc_net` bridge and does not
modify unrelated containers. Only the operator container currently receives
`NET_ADMIN`; this will be used later to apply `tc netem` to that container's
egress without changing a host interface.
