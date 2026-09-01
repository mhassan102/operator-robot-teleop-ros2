# Architecture

Milestone 1 defines two logical services built from a common ROS 2 Humble image:

- `operator`: future keyboard control, heartbeat, and monitoring;
- `robot`: future safety controller, simulator, telemetry, and camera.

Both use CycloneDDS on a dedicated Docker bridge with ROS domain ID 42 by
default. The common image reduces build time while Compose keeps runtime roles
separate.

## Milestone 2 command path

Milestone 2 published a bare `geometry_msgs/msg/Twist` on `/cmd_vel_raw`. That
topic is no longer the command path.

## Milestone 3 command path

```text
operator_command (operator container)
  -> /teleop/command [teleop_demo_msgs/msg/TeleopCommand]
        sequence, stamp, twist, session_id
  -> CycloneDDS over ros2_teleop_poc_net
  -> robot_command_receiver (robot container)
  -> /teleop/ack [teleop_demo_msgs/msg/TeleopAck]
        sequence, session_id, source_stamp, receive_stamp, disposition
  -> operator_command latency / RTT stats
```

The custom command still carries a standard `Twist` so a later safety layer can
extract `/cmd_vel_safe` for the simulator. `/teleop/command` is not a motor
command.

Command and acknowledgement topics use RELIABLE, VOLATILE, KEEP_LAST depth 10.
That is a small queue so stale commands do not pile up, while an unimpaired
local network still has a zero-loss baseline.

Rates and velocity limits come from `config/teleop.yaml`. The operator clamps
linear and angular velocity before publishing. Each operator run creates a new
`session_id`; the robot resets sequence counters when the session changes.

One-way command age is `receive_stamp - source_stamp`. Acknowledgement RTT is
operator receive time minus `source_stamp`. Both are valid on this prototype
because the containers share the host clock.
