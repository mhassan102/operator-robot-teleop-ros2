# Testing

Milestone 1 is accepted when:

1. the image builds;
2. the resolved Compose configuration is valid;
3. operator and robot become healthy;
4. both source ROS 2 Humble;
5. both report CycloneDDS and the same ROS domain ID; and
6. unrelated containers retain their baseline state.

## Milestone 2

Start the project and run the deterministic transport test:

```bash
./scripts/start.sh
./scripts/test_basic.sh
```

The test verifies cross-container ROS discovery, receipt of Cartesian `+x`,
`x-`, `+y`, `+yaw`, and `stop` commands, an exact 100-message burst, recovery
after restarting the robot container, and isolation from a different ROS domain
ID. It uses `/teleop/command` (`teleop_demo_msgs/msg/TeleopCommand`). It exits
nonzero and prints robot logs on failure.

## Milestone 3

```bash
./scripts/start.sh
./scripts/test_delivery.sh
```

The test verifies:

1. `/teleop/command` and `/teleop/ack` use the custom message types;
2. a deterministic 20 Hz sequence for 30 seconds (600 commands) has zero loss
   and monotonic sequence numbers on the unimpaired local network;
3. current, minimum, maximum, and average one-way latency and RTT are logged;
4. timestamps are consistent (non-negative one-way age, RTT >= one-way);
5. a second unimpaired burst also has zero acknowledgement loss;
6. `--inject duplicate` increments the robot duplicate counter;
7. `--inject reorder` increments the robot out-of-order counter; and
8. a new operator session logs `SESSION RESET` and starts counters at the new
   session's received count.

Unit tests in `teleop_demo/test` cover command parsing, six-axis clamping,
sequence fault lists, delivery/latency counters, and the watchdog state
machine without DDS.

## Milestone 4

```bash
./scripts/start.sh
./scripts/test_watchdog.sh
```

The test verifies:

1. the robot latches `SAFE STOP ACTIVATED` at startup;
2. a `+x` jog appears on `/cmd_vel_safe` at 0.05 m/s;
3. stopping the operator produces `TIMEOUT` and a zero safe twist;
4. a heartbeat restores the connection without replaying the last jog;
5. a fresh command is required before `/cmd_vel_safe` is non-zero again; and
6. with heartbeat still running, command silence zeros the jog without a new
   watchdog timeout.

Connection policy is `command_or_heartbeat` as documented in
`config/teleop.yaml`. `heartbeat_only` is covered by unit tests.

## Milestone 5

```bash
./scripts/start.sh
./scripts/test_sim.sh
```

The headless test waits for `/teleop/tool_pose`, records the tool position,
sends a 2-second `+x` jog, asserts the pose changed, then checks that the
watchdog stops further motion. `./scripts/start.sh --gui` is optional and
requires a working `DISPLAY`.
