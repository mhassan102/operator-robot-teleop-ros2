# Remote teleoperation POC

Lab stack for remote teleoperation of a 6-DOF arm: ROS 2 operator +
robot, Zenoh, userspace multi-link transport, a software safety
gateway, and a Jetson camera preview.

This is a **POC moving toward a product**. It is not Adamo (we own the
motion stack and the operator backend; they own a cloud agent + UI and
do not sell the arm).

**Start here:** [`IMPLEMENTATION.md`](IMPLEMENTATION.md) — status
board, target architecture, feature contracts, and session prompts.

## Layout

| Path | What |
| ---- | ---- |
| [`IMPLEMENTATION.md`](IMPLEMENTATION.md) | Product plan (root, this is the high-level doc) |
| [`teleoperation-prototype/`](teleoperation-prototype/) | ROS 2 Humble Compose: operator + robot, MoveIt Servo, Gazebo, keyboard teleop |
| [`mlink-transport/`](mlink-transport/) | Userspace UDP bonding (`mlink-op` / `mlink-edge`), stages 0–3 done. Default doc: [`mlink-transport/README.md`](mlink-transport/README.md) |
| [`safety/`](safety/) | 25-function coverage + Zone 1 plan (Safety-A integration on hold) |
| [`video/`](video/) | Lab WebRTC / NVENC notes (code still on the Orin, not in git) |

Feature-specific plans live **in those directories**, including work
that is on hold. Only the product roadmap stays at the repo root.

## What already works

- Two ROS 2 Humble containers, `TeleopCommand` over **Zenoh**, 500 ms
  watchdog in front of MoveIt Servo, Gazebo 6-DOF arm, keyboard jog,
  named poses.
- **mlink** duplicate-on-all-up-paths, first-good delivery, Ethernet +
  Wi-Fi cable-pull between this PC and Orin `nvidia-3`.
- Orin USB camera → **`nvv4l2h264enc`** → MediaMTX → Chrome (separate
  tab, Tailscale ICE, tree not in this repo).

## What is next

F5.3: fill the console HUD from `/teleop/state` and `/teleop/tool_pose`
(CONNECTED / TIMEOUT / pose). See `IMPLEMENTATION.md` Feature F5.
Camera is still a placeholder (F6).

## Quick run (local teleop)

```bash
cd teleoperation-prototype
./scripts/start.sh --gui
# console (keys + heartbeat; HUD in F5.3): http://127.0.0.1:8090/
./scripts/keyboard_teleop.sh   # TTY fallback
```

mlink tests: `cd mlink-transport && python3 -m pytest`.
