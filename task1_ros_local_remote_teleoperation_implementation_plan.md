# Implementation Plan: Local ROS 2 Remote Teleoperation Prototype

## 1. Purpose and completion target

This plan implements the prototype described in
`task1_ros_local_remote_teleoperation_protoype.md` as a sequence of small,
independently verified milestones.

The final proof of concept will run locally on the Ubuntu 22.04 host and provide:

- separate operator-side and robot-side containers;
- ROS 2 Humble communication through CycloneDDS;
- keyboard and scripted velocity commands;
- a differential-drive robot in a lightweight Gazebo simulation;
- odometry, state, heartbeat, watchdog, and safe-stop behavior;
- command sequence and latency measurements;
- container-scoped delay, jitter, and packet-loss injection;
- DDS/RTPS packet capture and inspection;
- a simulated camera/video path; and
- terminal-based monitoring and repeatable tests.

Implementation will stop at each milestone until its acceptance tests pass. A
later phase will not be used to hide or work around a failure in an earlier one.

## 2. Constraints and working assumptions

- Host: Ubuntu 22.04, x86-64, Docker Engine 28, Docker Compose v2.
- Available resources: 20 logical CPUs, approximately 15 GiB RAM, and 82 GiB
  free disk at the time of assessment.
- ROS and simulator dependencies will live in images, not on the host.
- No cloud service or external API is needed at runtime.
- Internet access is needed initially to pull images and Ubuntu/ROS packages.
- Existing Docker workloads must not be stopped, renamed, or modified.
- The project will use explicit Compose project/container/network names to avoid
  colliding with existing containers.
- The first simulator target is a custom 6-DOF arm with a simple gripper, not
  a differential-drive mobile robot. A minimal custom model is preferred over
  a full industrial arm / MoveIt stack if it gives a smaller Humble/Gazebo
  dependency set. MoveIt Servo can be added after the arm moves from
  `/cmd_vel_safe`.
- Gazebo will support GUI and headless modes. Headless mode is the required path
  for automated testing; GUI mode is for interactive demonstrations.
- Network impairment will be applied only to a container's `eth0` on a dedicated
  Compose bridge network. It will never modify the host's primary interface.
- One-way latency is valid locally because all containers share the host clock.
  RTT will also be supported or documented for future multi-host operation.

## 3. Target architecture

```text
Host keyboard/display
        |
        v
+---------------- operator container ----------------+
| teleop input -> stamped commands -> status monitor  |
| heartbeat     <- telemetry/ack <- image viewer      |
+-----------------------+-----------------------------+
                        | ROS 2 / CycloneDDS / UDP
              dedicated Compose bridge network
                        | optional netem on container eth0
+-----------------------v-----------------------------+
|                    robot container                  |
| receiver -> validation -> watchdog -> safe cmd_vel  |
|                                      |              |
| telemetry/ack/image <- Gazebo <- 6-DOF arm + jogger |
+-----------------------------------------------------+
```

The safety node will be the only remote-control path to the simulator:

```text
/teleop/command -> validation/watchdog -> /cmd_vel_safe -> Cartesian jogger -> Gazebo arm
```

Raw remote commands will never be connected directly to the simulated motors.

## 4. Planned repository layout

```text
teleoperation-prototype/
├── .env.example
├── README.md
├── compose.yaml
├── docker/
│   ├── Dockerfile
│   └── entrypoint.sh
├── config/
│   ├── cyclonedds.xml
│   └── teleop.yaml
├── ros2_ws/
│   └── src/
│       └── teleop_demo/
│           ├── package.xml
│           ├── setup.py
│           ├── resource/
│           ├── launch/
│           ├── config/
│           ├── worlds/
│           ├── models/
│           ├── teleop_demo/
│           └── test/
├── scripts/
│   ├── build.sh
│   ├── start.sh
│   ├── stop.sh
│   ├── shell_operator.sh
│   ├── shell_robot.sh
│   ├── test_basic.sh
│   ├── test_watchdog.sh
│   ├── test_network.sh
│   ├── network_good.sh
│   ├── network_medium.sh
│   ├── network_bad.sh
│   ├── network_very_bad.sh
│   ├── network_reset.sh
│   └── capture_dds.sh
└── docs/
    ├── architecture.md
    ├── networking.md
    └── testing.md
```

One common image will initially be used for reproducibility and build-cache
efficiency. Compose services will select operator or robot behavior through
their commands and profiles. The image can be split later if size or deployment
requirements justify it.

## 5. Milestone-by-milestone implementation

### Milestone 0: Record baseline and protect the host

Implementation:

1. Record Docker/Compose versions, architecture, free disk, active containers,
   display variables, and existing Docker networks.
2. Select a unique Compose project name, such as `ros2_teleop_poc`.
3. Define resource-conscious defaults and a cleanup procedure that removes only
   this project's containers and network.
4. Add a preflight script that checks Docker access, Compose, available disk,
   X11/Wayland inputs, and required Linux tools.
5. Record existing host `tc qdisc` state without changing it.

Verification and tests:

```bash
docker info
docker compose version
docker ps -a
docker network ls
tc qdisc show
```

Acceptance criteria:

- Docker is reachable from the implementation session.
- At least 25 GiB disk remains available before image builds.
- Existing container IDs and states are recorded and unchanged.
- No host network interface or queue discipline is modified.

### Milestone 1: Scaffold the containerized ROS 2 workspace

Implementation:

1. Create the planned directory structure.
2. Build a pinned Ubuntu 22.04/ROS 2 Humble image.
3. Install only required ROS packages, CycloneDDS RMW, build tools, networking
   diagnostics, and later simulator dependencies in documented layers.
4. Add an entrypoint that sources ROS 2 and the built workspace reliably.
5. Define `operator` and `robot` Compose services on a dedicated bridge network.
6. Set a project-specific `ROS_DOMAIN_ID` and `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`.
7. Mount only the display sockets needed for the optional GUI; do not use a
   privileged container.
8. Give `NET_ADMIN` only to the service whose egress will be impaired.

Verification and tests:

```bash
./scripts/build.sh
docker compose config
docker compose run --rm operator ros2 --help
docker compose run --rm operator printenv RMW_IMPLEMENTATION
docker compose run --rm robot printenv ROS_DOMAIN_ID
```

Acceptance criteria:

- Image builds without unpinned local host dependencies.
- Both service containers can start and source ROS 2.
- Both report the same domain ID and CycloneDDS implementation.
- Compose creates only the intended project network and resources.
- Existing Docker workloads remain healthy.

### Milestone 2: Prove ROS 2/DDS discovery and basic command transport

Implementation:

1. Create the Python `ament_python` package `teleop_demo`.
2. Implement a scripted operator publisher using
   `geometry_msgs/msg/Twist` on `/cmd_vel_raw`.
3. Implement a robot receiver that logs receive time and velocity values.
4. Configure CycloneDDS for the Compose bridge. Start with multicast discovery;
   add service-name/static peers only if this environment requires them.
5. Add health checks and bounded discovery/test timeouts.
6. Add `test_basic.sh` to publish known commands non-interactively.

Verification and tests:

```bash
./scripts/start.sh --core
docker compose exec operator ros2 node list
docker compose exec operator ros2 topic info /cmd_vel_raw --verbose
./scripts/test_basic.sh
```

Test cases:

- Publish forward, reverse, left, right, and stop values.
- Confirm the robot receives the exact values in order.
- Restart one container and confirm discovery recovers automatically.
- Confirm a different `ROS_DOMAIN_ID` prevents discovery in an isolated test.

Acceptance criteria:

- Operator and robot discover each other without host ROS installation.
- At least 100 scripted commands are received in order on a normal local network.
- Stop is received and logged as zero linear and angular velocity.
- Repeated start/stop is reliable.

### Milestone 3: Add command metadata and measurable delivery

Implementation:

1. Define a small custom command message containing a `Twist`, sequence number,
   and source timestamp, while retaining standard `Twist` at the simulator edge.
2. Publish at configurable rates and velocity limits from YAML.
3. Add a robot acknowledgement containing sequence and receive timestamp.
4. Track received, missing, duplicate, and out-of-order sequence numbers.
5. Compute local one-way command age and acknowledgement RTT.

Verification and tests:

- Send a deterministic sequence at 20 Hz for at least 30 seconds.
- Assert sequence monotonicity and expected count on an unimpaired network.
- Confirm timestamps use ROS/system time consistently.
- Inject a deliberately duplicated or reordered test sequence and verify counters.

Acceptance criteria:

- Current/minimum/maximum/average latency and packet counters are available.
- Local baseline loss is zero in repeated test runs.
- Statistics reset predictably when a new session starts.

### Milestone 4: Add the safety gateway, heartbeat, and watchdog

Implementation:

1. Route stamped commands into a robot-side safety controller.
2. Validate finite numeric values and clamp configured linear/angular limits.
3. Publish only validated output as `/cmd_vel_safe`.
4. Add an operator heartbeat topic and robot heartbeat tracking.
5. Add a configurable 500 ms watchdog using a monotonic/ROS timer.
6. Latch zero velocity on timeout and publish/log explicit state transitions:
   `CONNECTED`, `TIMEOUT`, `SAFE STOP ACTIVATED`, and `RESTORED`.
7. Require a fresh valid command after restoration; never replay a stale command.

Verification and tests:

- Unit-test velocity clamping, NaN/Inf rejection, and timeout calculations.
- Publish excessive velocities and assert the safe topic stays within limits.
- Pause/stop the operator and measure time until zero output.
- Restore the operator and verify movement resumes only after a fresh command.
- Stop heartbeat while commands continue, and vice versa, to validate the chosen
  connection policy documented in configuration.

Acceptance criteria:

- Safe stop occurs within the configured timeout plus one controller period.
- Zero commands continue to be issued or latched safely during disconnection.
- Invalid and stale commands never reach `/cmd_vel_safe`.
- All state changes appear once, clearly, in logs and telemetry.

### Milestone 5: Integrate a 6-DOF arm in Gazebo

Implementation:

1. Add Gazebo Classic compatible with ROS 2 Humble, plus `ros2_control` and
   `gazebo_ros2_control`.
2. Create a minimal world and a custom 6-DOF arm with a simple gripper. Do not
   use a differential-drive / TurtleBot model.
3. Convert `/cmd_vel_safe` Cartesian tool Twist into joint motion with a
   damped-least-squares Jacobian jogger. Never connect `/teleop/command`
   directly to Gazebo.
4. Publish TF (`base_link` → `tool0`) and `/teleop/tool_pose`.
5. Apply `/gripper_safe` to the gripper joints.
6. Add headless (required) and GUI Compose/start modes. Use software rendering
   as the GUI fallback.

Verification and tests:

Headless automated test:

1. Wait until `/joint_states` and `/teleop/tool_pose` are available.
2. Record the initial tool pose.
3. Command `+x` for a fixed duration.
4. Assert the tool pose changed beyond a tolerance.
5. Trigger watchdog and assert the tool stops moving.

GUI demonstration:

```bash
./scripts/start.sh --gui
./scripts/shell_operator.sh
```

Acceptance criteria:

- Simulation reaches a ready/clock-publishing state within a bounded timeout.
- Scripted Cartesian jogs produce measurable tool-pose changes.
- Watchdog stops the simulated arm.
- GUI visibly shows motion when display forwarding is available.
- All required automated checks also work without the GUI.

### Milestone 6: Add robot telemetry and operator monitoring

Implementation:

1. Use odometry as the authoritative position and velocity source.
2. Publish structured robot state including connectivity and watchdog status.
3. Implement a terminal monitor showing command rate/age, heartbeat age, robot
   pose/velocity, loss/order counts, latency statistics, and watchdog state.
4. Make telemetry and screen-refresh rates configurable.
5. Avoid clearing output in ways that make logs or automated tests unreadable;
   support both dashboard and line-oriented modes.

Verification and tests:

- Compare displayed position/velocity with direct `/odom` samples.
- Stop telemetry and verify the monitor marks it stale.
- Trigger and restore the watchdog and verify monitor state transitions.
- Run the monitor non-interactively and validate machine-readable output.

Acceptance criteria:

- Every requested status field is visible and correctly updated.
- Stale data is clearly distinguished from a valid zero value.
- Monitor failure cannot interfere with the safety controller.

### Milestone 7: Add safe network impairment profiles

Implementation:

1. Add good, medium, bad, very-bad, and reset scripts matching the requirements.
2. Resolve and validate the target container and its `eth0` interface explicitly.
3. Display the current qdisc before and after every change.
4. Apply `netem` only inside the selected project container using its limited
   `NET_ADMIN` capability.
5. Make reset idempotent and run it automatically during normal teardown.
6. Document that egress impairment direction depends on the selected container;
   support operator-to-robot and robot-to-operator tests separately if needed.

Profiles:

```text
good:      0-5 ms delay, 0% loss
medium:    50 ms delay, 10 ms jitter, 0.5% loss
bad:       150 ms delay, 40 ms jitter, 3% loss
very-bad:  300 ms delay, 80 ms jitter, 10% loss
```

Verification and tests:

- Capture host `tc qdisc show` before and after and assert it is unchanged.
- Apply each profile and verify the container qdisc parameters.
- Measure latency over enough samples to distinguish each profile statistically.
- Verify loss counters increase for lossy profiles.
- Reset twice and confirm normal baseline returns without error.

Acceptance criteria:

- Measured delay/loss tracks each configured profile within statistical tolerance.
- Watchdog activates under sufficiently severe command/heartbeat interruption.
- The host interface and unrelated containers remain unaffected.
- Reset always removes the project container's impairment.

### Milestone 8: Inspect DDS/RTPS traffic

Implementation:

1. Add a capture helper that identifies the project network/interface instead of
   hard-coding Docker-generated bridge names.
2. Capture from inside a project container where possible, or from the resolved
   project bridge with explicit user confirmation if host capture is necessary.
3. Document CycloneDDS discovery/data traffic and common RTPS identification.
4. Save optional `.pcap` output under a project artifact directory excluded from
   source control.

Verification and tests:

- Start a bounded capture, publish commands, and stop capture automatically.
- Assert the capture is non-empty.
- Use `tcpdump`/`tshark` filters to demonstrate UDP/RTPS traffic.
- Correlate command activity with packet timestamps without claiming that every
  UDP packet maps one-to-one to an application message.

Acceptance criteria:

- A repeatable command captures and identifies DDS traffic.
- Documentation remains valid if the Compose bridge name changes.
- Capture does not require promiscuous access to unrelated host traffic.

### Milestone 9: Add the simulated video path

Implementation:

1. Enable the robot camera sensor only after control/safety paths are stable.
2. First publish `sensor_msgs/msg/Image` plus camera information through ROS 2.
3. Add a lightweight operator viewer or image-rate/age subscriber; GUI display is
   optional for automated tests.
4. Configure video resolution, frame rate, and QoS separately from control QoS.
5. Record bandwidth and latency behavior under network profiles.
6. Evaluate a minimal GStreamer compressed/UDP path only after the ROS image path
   passes. Keep it as an optional comparison, not a prerequisite for core safety.

Verification and tests:

- Confirm image dimensions, encoding, timestamps, and nonzero frame rate.
- Confirm frames arrive at the operator and become stale when the source stops.
- Measure topic bandwidth on normal and impaired networks.
- Verify heavy video traffic does not bypass or disable the watchdog.
- Compare control latency with video disabled and enabled.

Acceptance criteria:

- Operator receives and can display simulated camera frames.
- Monitoring reports video frame rate and age.
- Control safety remains functional while video is active or degraded.
- The difference between control and video traffic is documented with measurements.

### Milestone 10: End-to-end automated and manual acceptance suite

Implementation:

1. Combine unit, ROS integration, Compose, simulation, watchdog, network, and
   capture tests into bounded scripts with clear exit codes.
2. Ensure tests collect relevant container logs on failure.
3. Add a clean-room procedure: build, start, test, stop, and rebuild.
4. Add an interactive demonstration procedure for keyboard, GUI, monitoring,
   impairments, watchdog, packet capture, and video.

Required end-to-end tests:

1. Containers build and start correctly.
2. CycloneDDS discovery works across the project network.
3. Operator publishes and robot receives known commands.
4. Safety validation clamps/rejects invalid commands.
5. Simulated robot translates and rotates.
6. Odometry and robot state reach the operator.
7. Heartbeats maintain connected state.
8. Loss of communication produces safe stop within tolerance.
9. A fresh command safely restores operation.
10. Netem profiles measurably affect delay and loss.
11. Sequence counters observe induced loss/out-of-order delivery where present.
12. DDS traffic is captured and identifiable.
13. Camera frames reach the operator.
14. Video load does not break safe-stop behavior.
15. Teardown removes only this project's resources and resets impairment.

Acceptance criteria:

- The full headless suite passes repeatedly from a clean project state.
- The interactive GUI demonstration works or a documented host-specific display
  fallback is proven while headless acceptance remains green.
- Failures produce actionable logs rather than hanging indefinitely.
- Existing containers remain running and unchanged.

### Milestone 11: Documentation and final handoff

Implementation:

1. Write a beginner-oriented README with quick start and cleanup commands.
2. Explain ROS 2, DDS, topics, `cmd_vel`, telemetry, heartbeat, watchdog, QoS,
   latency, jitter, packet loss, stale commands, and safe stop.
3. Document the exact architecture, topic/message map, and safety data flow.
4. Document GUI/headless modes, CycloneDDS settings, netem directionality,
   packet capture, troubleshooting, and expected resource use.
5. Record known POC limitations and the changes required for real hardware or a
   genuinely remote multi-host deployment.
6. Include a tested command transcript and expected observable results.

Verification and tests:

- Follow the README from a clean shell without relying on undocumented commands.
- Validate every documented script and configuration path.
- Check that defaults in documentation match YAML and Compose configuration.
- Run teardown and confirm no project containers/network remain.

Acceptance criteria:

- A new user can reproduce the POC using only the README.
- All requested concepts and tests are documented.
- Limitations are explicit, especially local-clock latency, simulator realism,
  DDS security, internet/WAN discovery, and physical-robot safety certification.

## 6. ROS 2 interface design to confirm during implementation

The exact custom message names can change during scaffolding, but the semantic
interfaces should remain stable:

| Topic | Direction | Type/purpose |
|---|---|---|
| `/teleop/command` | operator to robot | Stamped command with sequence and `Twist` |
| `/teleop/heartbeat` | operator to robot | Stamped heartbeat/session identity |
| `/cmd_vel_safe` | safety to simulator | `geometry_msgs/msg/Twist` |
| `/odom` | simulator to robot/operator | `nav_msgs/msg/Odometry` |
| `/teleop/ack` | robot to operator | Sequence, receive time, safety disposition |
| `/teleop/state` | robot to operator | Connectivity/watchdog/robot state |
| `/camera/image_raw` | robot to operator | `sensor_msgs/msg/Image` |

QoS will be selected per data class and tested under loss:

- command/heartbeat: small queues designed to avoid executing stale commands;
- safety state: reliable or transient behavior where appropriate;
- odometry/video: sensor-data-style best effort where current data matters more
  than retransmitting old samples.

## 7. Test strategy and evidence

Each milestone will produce evidence in one or more of these forms:

- command exit status and bounded logs;
- ROS topic/node discovery output;
- automated assertions from unit/integration tests;
- before/after odometry values;
- watchdog timing measurements;
- latency/loss summaries with sample counts;
- container-local qdisc output plus unchanged host qdisc output;
- bounded packet captures; and
- screenshots only for the optional interactive GUI demonstration.

Tests will avoid arbitrary sleeps where readiness checks are possible. Every
long-running process will have a timeout, health check, or explicit stop path.

## 8. Resource and risk controls

- Check disk before builds; use targeted Docker cleanup only for this project.
- Pin the base image and key packages as far as Ubuntu/ROS repositories permit.
- Build headless simulation first to reduce GUI troubleshooting risk.
- Prefer a small custom robot/world to control dependency and startup cost.
- Never use `--privileged`; grant only `NET_ADMIN` where netem needs it.
- Never run host-level `tc` modifications from convenience scripts.
- Keep safety control independent of monitoring and video nodes.
- Treat a stopped/paused/crashed operator, delayed messages, invalid values, and
  stale messages as explicit failure cases.
- Preserve all unrelated Docker resources.

## 9. Definition of done

The POC is complete only when a clean, documented run demonstrates:

```text
keyboard/scripted input
  -> operator container
  -> ROS 2 / CycloneDDS over dedicated Docker network
  -> robot validation and watchdog
  -> safe velocity command
  -> simulated differential-drive robot motion
  -> odometry/state/video returned to operator
  -> monitoring, latency/loss measurement, and DDS inspection
```

All fifteen end-to-end tests in Milestone 10 must pass, safe stop must be proven
under communication loss, network impairment must remain container-scoped, and
the existing Docker workloads must remain unaffected.

