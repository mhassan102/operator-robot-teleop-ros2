# Architecture

Product roadmap: [`../../IMPLEMENTATION.md`](../../IMPLEMENTATION.md).

Milestone 1 defines two logical services built from a common ROS 2 Humble image:

- `operator`: scripted Cartesian jog, heartbeat, and future keyboard/monitor;
- `robot`: delivery tracking, safety gateway, MoveIt Servo, named-pose planning, and Gazebo arm.

Both use **rmw_zenoh_cpp** on a dedicated Docker bridge with ROS domain ID 42
by default. The robot container runs `rmw_zenohd`; the operator connects as a
Zenoh client to `tcp/robot:7447`. CycloneDDS remains in the image as a fallback
(`RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`).

## Command path

```text
operator_command / operator_heartbeat
  -> /teleop/command   [TeleopCommand: seq, stamp, frame_id, session, Twist, gripper]
  -> /teleop/heartbeat [TeleopHeartbeat]
  -> rmw_zenoh_cpp over ros2_teleop_poc_net (robot rmw_zenohd)
  -> robot_command_receiver
        delivery stats + ack
        safety: validate, clamp, watchdog
  -> /teleop/ack       [TeleopAck]
  -> /cmd_vel_safe     [geometry_msgs/Twist]   Cartesian tool jog, never raw
  -> /gripper_safe     [std_msgs/Float64]
  -> /teleop/state     [TeleopState]
  -> servo_bridge (Twist -> TwistStamped, gripper, TF pose)
  -> MoveIt Servo
  -> /arm_controller/joint_trajectory
  -> Gazebo 6-DOF arm + gripper
  -> /teleop/tool_pose [geometry_msgs/PoseStamped]
```

Named poses (`home`, `fold`, `ready`, `observe`, `pregrasp`, `retract`, `stow`)
use `/teleop/go_named_pose` and `move_group`. They are not `TeleopCommand`
fields. Servo is stopped for the trajectory, then started again. Gripper open /
close stays on the keyboard (`g` / `h`); named poses set arm joints only.

`Twist` is interpreted as a 6-DOF Cartesian **tool rate** (`linear` m/s,
`angular` rad/s) in `command_frame` (default `tool0`). It is not a wheeled-base
velocity. The gripper is not part of Twist.

`/cmd_vel_safe` is the only teleop motion input into Servo. Raw
`/teleop/command` never reaches Gazebo. The operator wire is unchanged from M6.

## Safety policy

- Invalid (NaN/Inf) commands are rejected and do not keep the watchdog alive.
- Linear and angular axes are clamped to YAML limits (0.1 m/s, 0.3 rad/s).
- `watchdog_keep_alive: command_or_heartbeat`: either a valid command or a
  heartbeat resets the 500 ms watchdog.
- Cartesian jog is a rate: if commands stop while still CONNECTED, the safe
  twist is zeroed after `command_timeout_ms` (200 ms). Last non-zero twist is
  never replayed after a watchdog timeout.
- Logged once per transition: `CONNECTED`, `TIMEOUT`, `SAFE STOP ACTIVATED`,
  `RESTORED`. After `RESTORED`, motion resumes only on a fresh command.

One-way command age is `receive_stamp - source_stamp`. Acknowledgement RTT is
operator receive time minus `source_stamp`. Both are valid on this prototype
because the containers share the host clock.
