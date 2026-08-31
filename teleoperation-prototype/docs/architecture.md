# Architecture

Milestone 1 defines two logical services built from a common ROS 2 Humble image:

- `operator`: future keyboard control, heartbeat, and monitoring;
- `robot`: future safety controller, simulator, telemetry, and camera.

Both use CycloneDDS on a dedicated Docker bridge with ROS domain ID 42 by
default. The common image reduces build time while Compose keeps runtime roles
separate.

## Milestone 2 command path

```text
operator_command (operator container)
  -> /cmd_vel_raw [geometry_msgs/msg/Twist]
  -> CycloneDDS over ros2_teleop_poc_net
  -> robot_command_receiver (robot container)
```

The publisher waits for a subscriber before sending, and the receiver logs each
command with its receive timestamp. Command safety and the simulator-facing
topic are introduced in later milestones; `/cmd_vel_raw` is not a motor command.
