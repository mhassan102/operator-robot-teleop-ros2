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

The test verifies cross-container ROS discovery, receipt of forward, backward,
left, right, and stop commands, an exact 100-message burst, recovery after
restarting the robot container, and isolation from a different ROS domain ID.
It now uses `/teleop/command` (`teleop_demo_msgs/msg/TeleopCommand`) instead of
the Milestone 2 `/cmd_vel_raw` Twist topic. It exits nonzero and prints robot
logs on failure.

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

Unit tests in `teleop_demo/test` cover command parsing, velocity clamping,
sequence fault lists, and the delivery/latency counters without DDS.
