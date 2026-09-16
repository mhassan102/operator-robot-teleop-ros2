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
| [`mlink-transport/`](mlink-transport/) | Userspace UDP bonding (`mlink-op` / `mlink-edge`), stages 0–3 and 5 done. Default doc: [`mlink-transport/README.md`](mlink-transport/README.md) |
| [`safety/`](safety/) | 25-function coverage + Zone 1 plan (Safety-A integration on hold) |
| [`video/`](video/) | Orin WebRTC / NVENC scripts + yaml (MediaMTX binary not in git) |

Feature-specific plans live **in those directories**, including work
that is on hold. Only the product roadmap stays at the repo root.

## What already works

- Two ROS 2 Humble containers, `TeleopCommand` over **Zenoh**, 500 ms
  watchdog in front of MoveIt Servo, Gazebo 6-DOF arm, keyboard jog,
  named poses.
- **mlink** duplicate-on-all-up-paths, first-good delivery, Ethernet +
  Wi-Fi cable-pull between this PC and Orin `nvidia-3`. Control and
  camera RTP ride that pipe (F8). Apps talk `127.0.0.1` only.
- Orin USB camera → **`nvv4l2h264enc`** → mlink media → localhost
  MediaMTX → same console tab (WHEP). Tailscale is SSH only.

## What is next

F8 is done. F7 (Orin HW encode verify) and F10 (Safety-A) are on hold.
F9 (5G) is blocked until the dongle is on the Orin. See
`IMPLEMENTATION.md`. F8 bring-up: [`mlink-transport/docs/f8_usage.md`](mlink-transport/docs/f8_usage.md).

## Quick run (local teleop)

```bash
cd teleoperation-prototype
./scripts/start.sh --gui
# console: http://127.0.0.1:8090/
./scripts/keyboard_teleop.sh   # TTY fallback
```

mlink tests: `cd mlink-transport && python3 -m pytest`.
