# Task: Build a Local ROS 2 Remote Teleoperation Prototype on Ubuntu 22.04

I want to build a small end-to-end remote teleoperation prototype on my Ubuntu 22.04 development machine.

The purpose of this prototype is not to build a production robot system yet. The goal is to create a practical learning and testing environment where I can understand how remote teleoperation works, including ROS 2 communication, simulated robot control, video/telemetry flow, network latency, packet loss, watchdog behavior, and DDS networking.

Please inspect the current workspace first, then create the required project structure, configuration files, Dockerfiles, scripts, and documentation.

## Environment

Host OS:

Ubuntu 22.04

Use:

- Docker / Docker Compose
- ROS 2 Humble
- Gazebo or another ROS 2-compatible lightweight robot simulator
- CycloneDDS if practical
- Python or C++ ROS 2 nodes
- Linux networking tools such as tc/netem
- tcpdump/Wireshark-compatible traffic
- Bash scripts for startup/testing

Keep the prototype fully local initially. No cloud service or external API should be required.

## Target Architecture

Create two logical sides:

```text
                Ubuntu 22.04 Host

     Operator Side                Robot Side
   ┌─────────────────┐        ┌────────────────────┐
   │ ROS 2           │        │ ROS 2              │
   │ Keyboard Teleop │        │ Robot Controller   │
   │ Status Viewer   │        │ Gazebo Simulator   │
   │ Video Viewer    │        │ Camera/Sensors     │
   └────────┬────────┘        └─────────┬──────────┘
            │                           │
            │       ROS 2 / DDS         │
            └────────── Network ─────────┘
```

Prefer separate Docker containers for the operator and robot sides.

For the first working version, using Docker host networking is acceptable if it simplifies ROS 2 DDS discovery.

Later the networking should be easy to change to isolated Docker networks.

## Phase 1: Basic ROS 2 Communication

Create:

1. Operator container
2. Robot container
3. ROS 2 Humble installation in both containers
4. DDS communication between them

Implement a simple control flow:

```text
Keyboard
   ↓
Operator ROS 2 Node
   ↓
/cmd_vel
   ↓
DDS
   ↓
Robot ROS 2 Node
```

Use the standard ROS geometry message where appropriate, preferably:

```text
geometry_msgs/msg/Twist
```

The operator should be able to issue commands such as:

```text
forward
backward
turn left
turn right
stop
```

The robot-side node should print received velocity commands with timestamps and sequence information where useful.

## Phase 2: Simulated Mobile Robot

Add a lightweight simulated mobile robot.

Prefer something commonly supported by ROS 2 Humble, such as TurtleBot3 or another simple differential-drive robot.

The objective is:

```text
Keyboard
   ↓
ROS 2 teleoperation
   ↓
/cmd_vel
   ↓
Simulated robot
   ↓
Robot moves in simulator
```

Avoid unnecessary complexity.

I want to clearly see the robot moving based on commands coming from the operator side.

## Phase 3: Robot Telemetry

Add a robot telemetry publisher.

Publish at least:

```text
robot position
linear velocity
angular velocity
robot state
timestamp
communication status
```

If simulator data is available, use real simulated odometry.

Prefer standard ROS messages where possible, such as:

```text
nav_msgs/msg/Odometry
```

The operator side should subscribe and print/display the robot status.

Example:

```text
Robot position: x=2.3 y=1.5
Linear velocity: 0.8 m/s
Angular velocity: 0.2 rad/s
Last telemetry age: 12 ms
```

## Phase 4: Heartbeat and Communication Watchdog

Add a simple teleoperation heartbeat mechanism.

Operator publishes heartbeat messages periodically.

Example:

```text
Operator
   ↓
/teleop/heartbeat
   ↓
Robot
```

Robot keeps track of the last valid heartbeat or control command.

If no valid command/heartbeat is received within a configurable timeout, for example:

```text
500 ms
```

the robot must automatically issue a zero-velocity safe-stop command.

Conceptually:

```text
if current_time - last_command_time > watchdog_timeout:
    stop_robot()
```

The timeout must be configurable.

Log clearly when watchdog state changes:

```text
TELEOP CONNECTED
TELEOP TIMEOUT
SAFE STOP ACTIVATED
TELEOP RESTORED
```

## Phase 5: Network Impairment Simulation

Create scripts that use Linux `tc netem` so I can emulate realistic remote network conditions.

Provide commands or helper scripts for:

### Normal network

```text
0-5 ms delay
no loss
```

### Moderate remote connection

```text
50 ms delay
10 ms jitter
0.5% packet loss
```

### Poor connection

```text
150 ms delay
40 ms jitter
3% packet loss
```

### Very poor connection

```text
300 ms delay
80 ms jitter
10% packet loss
```

Provide scripts such as:

```text
scripts/network_good.sh
scripts/network_medium.sh
scripts/network_bad.sh
scripts/network_reset.sh
```

The scripts should clearly identify which interface they modify.

Do not silently modify the host's main network interface.

Prefer applying network impairment inside a container or on a dedicated Docker network/interface where possible.

## Phase 6: Latency Measurement

Add application-level timestamping so we can measure command latency.

For example:

```text
Operator sends command:

sequence = 152
timestamp = T1

Robot receives:

timestamp = T2

latency = T2 - T1
```

Print statistics such as:

```text
Current latency
Minimum latency
Maximum latency
Average latency
Packets received
Packets lost
Out-of-order messages
```

If one-way latency cannot be measured correctly because clocks are independent, document that limitation and use RTT instead.

Since everything initially runs on one Ubuntu host, host clock synchronization is sufficient for the first prototype.

## Phase 7: DDS Inspection

I want to understand ROS 2 networking underneath.

Prefer CycloneDDS if it can be configured cleanly with ROS 2 Humble.

Document:

```text
ROS 2 node
    ↓
DDS implementation
    ↓
UDP/IP
    ↓
Linux network
```

Provide instructions for inspecting traffic with:

```bash
tcpdump
```

For example, document how to identify DDS/RTPS packets.

Do not hard-code assumptions if Docker networking changes the interface names.

## Phase 8: Video Prototype

After the basic control path is stable, add a simple simulated video path.

Preferred architecture:

```text
Virtual camera
     ↓
Robot container
     ↓
ROS image topic OR GStreamer
     ↓
Network
     ↓
Operator side
     ↓
Display
```

Keep the first implementation simple.

A ROS 2 image topic is acceptable initially.

If GStreamer is easier or more representative for remote teleoperation, implement a minimal pipeline.

The purpose is to demonstrate that teleoperation commonly has two very different traffic paths:

```text
Control:
small packets
latency sensitive

Video:
large bandwidth
latency sensitive
```

## Phase 9: Monitoring

Create a simple terminal-based monitoring tool or ROS node showing:

```text
Teleop connection status
Command rate
Last command age
Heartbeat age
Robot velocity
Packet/sequence loss
Average latency
Maximum latency
Watchdog state
```

Example:

```text
================ TELEOP STATUS ================

Connection:        CONNECTED
Command rate:      20 Hz
Last command:      18 ms ago
Heartbeat age:     35 ms

Robot velocity:    0.72 m/s
Angular velocity:  0.10 rad/s

Average latency:   48 ms
Maximum latency:   91 ms
Lost commands:     2
Out-of-order:      0

Watchdog:          OK

===============================================
```

## Suggested Project Structure

Create something similar to:

```text
teleoperation-prototype/
│
├── docker/
│   ├── operator.Dockerfile
│   └── robot.Dockerfile
│
├── docker-compose.yml
│
├── ros2_ws/
│   └── src/
│       └── teleop_demo/
│           ├── operator_node/
│           ├── robot_node/
│           ├── telemetry/
│           ├── heartbeat/
│           └── monitoring/
│
├── config/
│   ├── cyclonedds.xml
│   └── teleop.yaml
│
├── scripts/
│   ├── build.sh
│   ├── start.sh
│   ├── stop.sh
│   ├── test_basic.sh
│   ├── network_good.sh
│   ├── network_medium.sh
│   ├── network_bad.sh
│   └── network_reset.sh
│
├── docs/
│   ├── architecture.md
│   ├── networking.md
│   └── testing.md
│
└── README.md
```

Adjust this structure if a better ROS 2 layout is appropriate.

## Configuration

Keep important parameters in YAML/configuration rather than hard-coding them.

For example:

```yaml
command_rate_hz: 20
heartbeat_rate_hz: 10
watchdog_timeout_ms: 500

max_linear_velocity: 1.0
max_angular_velocity: 1.0

telemetry_rate_hz: 10
```

## Safety Behavior

Even though this is only a simulation, implement the architecture as if a physical robot could eventually be connected.

Remote commands must not directly bypass safety logic.

Preferred conceptual flow:

```text
Remote command
      ↓
Teleoperation receiver
      ↓
Command validation
      ↓
Watchdog / safety layer
      ↓
Robot controller
      ↓
Simulated motors
```

Reject or clamp commands exceeding configured limits.

On communication timeout:

```text
linear velocity = 0
angular velocity = 0
```

## Testing

Create automated or semi-automated tests covering at least:

```text
1. Containers start correctly
2. ROS 2 discovery works
3. Operator can publish commands
4. Robot receives commands
5. Simulated robot moves
6. Telemetry reaches operator
7. Heartbeat works
8. Watchdog stops robot when communication disappears
9. Robot resumes correctly after communication returns
10. Artificial network latency affects measured latency
11. Packet loss can be observed
12. DDS traffic can be captured with tcpdump
```

Also provide a simple test procedure.

Example:

```text
Terminal 1:
./scripts/start.sh

Terminal 2:
start operator teleop

Terminal 3:
start monitoring

Test:
move robot forward

Then apply:
./scripts/network_bad.sh

Observe:
latency
packet loss
video delay
robot response

Then stop operator container and confirm:

SAFE STOP ACTIVATED
```

## Important Implementation Approach

Please build this incrementally.

Do not attempt all phases at once.

First achieve:

```text
Operator container
      ↓
ROS 2 / DDS
      ↓
Robot container
      ↓
cmd_vel received
```

Then verify it.

After that add:

```text
Gazebo robot
```

Then:

```text
telemetry
```

Then:

```text
heartbeat/watchdog
```

Then:

```text
network impairment
```

Then:

```text
latency statistics
```

Finally:

```text
video
```

At each stage:

1. build
2. run
3. verify
4. fix errors
5. document the working state
6. then continue

Do not leave placeholder code where a simple working implementation is possible.

## Expected First Milestone

The first milestone should be a working prototype where I can run something similar to:

```bash
./scripts/build.sh
./scripts/start.sh
```

and then control the simulated robot from an operator terminal.

I should be able to see:

```text
Operator command
      ↓
ROS 2 topic
      ↓
DDS communication
      ↓
Robot controller
      ↓
Gazebo simulated robot
```

and inspect the traffic using Linux networking tools.

Once that basic path is stable, continue with heartbeat, watchdog, telemetry, latency emulation, packet loss testing, monitoring, and video.

## Documentation Requirements

The README should explain the project for someone new to robotics.

Include:

```text
What ROS 2 is
What DDS is
What /cmd_vel represents
What telemetry means
What heartbeat means
What watchdog means
Why stale commands are dangerous
Why UDP/DDS is useful for real-time communication
What latency and jitter mean
How network impairment is simulated
How the safe-stop mechanism works
```

Also include an architecture diagram using plain text or Mermaid.

The final prototype should remain simple enough that I can use it as a learning environment and later extend it toward a real remote teleoperation system.