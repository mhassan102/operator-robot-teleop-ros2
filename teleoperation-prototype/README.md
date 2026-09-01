# ROS 2 Teleoperation Prototype

This directory contains the incremental local ROS 2 Humble teleoperation proof
of concept. Milestone 3 adds stamped commands, acknowledgements, and delivery
statistics on top of the CycloneDDS path verified in Milestone 2.

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

## Milestone 2 transport test

```bash
./scripts/start.sh
./scripts/test_basic.sh
```

## Milestone 3 delivery test

```bash
./scripts/start.sh
./scripts/test_delivery.sh
```

Publish one stamped command burst manually:

```bash
docker compose exec operator \
  /teleop/entrypoint.sh ros2 run teleop_demo operator_command \
    --direction forward --count 5 \
    --ros-args --params-file /teleop/config/teleop.yaml
docker compose logs robot
```

Valid directions are `forward`, `backward`, `left`, `right`, and `stop`.
The operator publishes `teleop_demo_msgs/msg/TeleopCommand` on `/teleop/command`
and waits for `teleop_demo_msgs/msg/TeleopAck` on `/teleop/ack`. Each command
has a session id, sequence number, and source timestamp. The robot logs
receive time, one-way age, and counters for missing, duplicate, and
out-of-order sequences.

The Compose project uses the dedicated `ros2_teleop_poc_net` bridge and does not
modify unrelated containers. Only the operator container currently receives
`NET_ADMIN`; this will be used later to apply `tc netem` to that container's
egress without changing a host interface.
