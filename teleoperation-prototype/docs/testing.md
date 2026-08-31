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

The test verifies cross-container ROS discovery, the
`geometry_msgs/msg/Twist` type, and receipt of forward, backward, left, right,
and stop commands. It also verifies an exact 100-message burst, recovery after
restarting the robot container, and isolation from a different ROS domain ID.
It exits nonzero and prints robot logs on failure.
