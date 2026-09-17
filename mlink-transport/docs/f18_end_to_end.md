# F18 end-to-end — console g/h to real SO-ARM gripper

First WAN-style teleop run: **web console on this PC → local backend → mlink (Tailscale) → robot ROS on Usama’s PC → real gripper**. Camera is in the same console tab; it is **not** through mlink.

This is gripper-only (id 6, ~48 tick steps). No Gazebo, no named poses, no MoveIt Servo, no joints 1–5.

| Role | Host | Tailscale |
| --- | --- | --- |
| Operator | this PC `pure-dev-muhammadhassan` | `100.95.150.54` |
| Robot | laptop `gt-dev-muhammadusama` (`usama@gt-dev-muhammadusama`) | `100.67.47.79` |

Confirm IPs with `tailscale ip -4` before start. Control path: browser WebSocket → operator backend → **mlink** (one Tailscale pair) → `robot_mlink_bridge` → `/gripper_safe` → Feetech. Local ROS on each box is CycloneDDS localhost, not Zenoh across the WAN.

Camera: laptop MediaMTX `http://100.67.47.79:8889/cam`. Console: `http://127.0.0.1:8090/?cam=http://100.67.47.79:8889/cam`.

Related: [`usage.md`](usage.md) (F18 Stage 4), [`f8_usage.md`](f8_usage.md) (Orin eth+wifi, not this laptop).

---

## Copy trees (this PC)

From `/home/muhammadhassan/robots`:

```bash
rsync -az --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' \
  mlink-transport/ usama@gt-dev-muhammadusama:/home/usama/hassan/mlink-transport/

rsync -az --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' \
  --exclude 'ros2_ws/build' --exclude 'ros2_ws/install' --exclude 'ros2_ws/log' \
  teleoperation-prototype/ \
  usama@gt-dev-muhammadusama:/home/usama/hassan/teleoperation-prototype/
```

If the laptop tree already has a symlink `install/` from Stage 2, you do not need a separate colcon build. A new empty tree: `./scripts/start_robot_mlink.sh --real-arm` will build once.

This PC, once:

```bash
cd /home/muhammadhassan/robots/teleoperation-prototype
./scripts/build_workspace_mlink.sh operator
```

Keep `TELEOP_ARM` / robot Compose **stopped** until step 4 below.

---

## Bring-up

Clear the gripper. Watch the camera **before** any key. One tap, not a held key.

### 1. Remote — camera

```bash
ssh usama@gt-dev-muhammadusama
/home/usama/teleops_hassan/video/start.sh
```

Open `http://100.67.47.79:8889/cam/` to confirm the stream.

### 2. Remote — mlink-edge (no `--reflect`)

```bash
ssh usama@gt-dev-muhammadusama
cd /home/usama/hassan/mlink-transport
./scripts/start_daemon.sh edge --remote-laptop
```

Leave this terminal running.

### 3. This PC — mlink-op (no `--control`)

```bash
cd /home/muhammadhassan/robots/mlink-transport
./scripts/start_daemon.sh op --remote-laptop
```

Leave this terminal running. Both logs should show `session=1` and `stats ts=up`.

### 4. Remote — robot container (real arm)

```bash
ssh usama@gt-dev-muhammadusama
cd /home/usama/hassan/teleoperation-prototype
./scripts/start_robot_mlink.sh --real-arm
```

Expect healthy, `TELEOP_ARM=real`, nodes `robot_command_receiver` + `feetech_gripper` + `robot_mlink_bridge`. No Gazebo. Log: gripper id 6, deadman torque-off until the console is CONNECTED.

### 5. This PC — operator backend + console

```bash
cd /home/muhammadhassan/robots/teleoperation-prototype
./scripts/start_operator_mlink.sh --remote-laptop
```

Open:

```text
http://127.0.0.1:8090/?cam=http://100.67.47.79:8889/cam
```

Wait for HUD **CONNECTED**. `TELEOP_GRIPPER_ONLY=1` (wasd / named poses ignored).

### 6. Keys (this PC, console focused)

- One tap **g** — tiny open; it holds.
- Wait ~2 s on camera.
- One tap **h** — close toward the start pose.

Do not hold the key. Do not press wasd. If joints 1–5 twitch, stop immediately.

---

## Soft stop

USB unplug is not available. `stop_mlink.sh` stops **Docker only**, not mlink daemons.

```bash
# this PC
cd /home/muhammadhassan/robots/teleoperation-prototype
./scripts/stop_mlink.sh
# Ctrl-C mlink-op

# remote
ssh usama@gt-dev-muhammadusama
cd /home/usama/hassan/teleoperation-prototype && ./scripts/stop_mlink.sh
# Ctrl-C mlink-edge
/home/usama/teleops_hassan/video/stop.sh
```

Closing the console tab (or stopping operator Compose) should trip the 500 ms watchdog and Feetech deadman. A short Tailscale `ts down` should also deadman; do not mash g/h.

---

## What this is not

- Orin F8 eth+wifi bonding (`lab-op.yaml` / `start_*_mlink.sh` without `--remote-laptop`)
- Video through mlink
- Full-arm jog / Servo / named poses
- Zenoh as the WAN transport
