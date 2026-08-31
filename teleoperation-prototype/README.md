# ROS 2 Teleoperation Prototype

This directory contains the incremental local ROS 2 Humble teleoperation proof
of concept. Milestone 1 provides the container foundation; ROS nodes and the
simulator are added in later milestones.

## Milestone 1 quick start

```bash
cp .env.example .env
./scripts/build.sh
./scripts/start.sh
docker compose exec operator ros2 --help
./scripts/stop.sh
```

The Compose project uses the dedicated `ros2_teleop_poc_net` bridge and does not
modify unrelated containers. Only the operator container currently receives
`NET_ADMIN`; this will be used later to apply `tc netem` to that container's
egress without changing a host interface.

