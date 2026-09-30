# P7 lab check

This check starts the gripper. The person at the robot watches the arm.
The gripper is clear of the table and of hands before Start. The first
motion is one tap of `g` or `h` after the HUD says CONNECTED. Do not
hold the key. Do not send any other motion key.

Leave `TELEOP_SUPERVISOR_DRY_RUN` unset on both PCs. When that variable
is `1`, the robot answers `dry-run` and the operator window does not
start mlink, Docker, or the console.

The password is printed in the robot terminal for this check. Do not
write it into git, a log, or this file. Do not print
`packaging/run/ice-op.yaml` or `packaging/run/ice-edge.yaml`.

## 0. This PC — pytest only

```bash
cd /home/muhammadhassan/robots
QT_QPA_PLATFORM=offscreen PYTHONPATH=. python3 -m pytest -q packaging/tests
```

Pass: the packaging tests pass. That run does not start mlink, Docker,
the camera, the console, or the arm, and it does not open a serial port.

The console page needs Qt WebEngine from apt. Installing it does not
start teleop:

```bash
sudo apt install python3-pyqt5 python3-pyqt5.qtwebengine
```

If `websockets` does not import:

```bash
/usr/bin/python3 -m pip install --user websockets
```

## 1. EC2 — leave signalling up

Signalling is already listening on TCP 8765. Desktop apps use

`ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765`

Confirm the process and leave it. Do not restart it. Coturn stays as
it is. Do not start a second listener.

```bash
ss -ltnp | grep 8765
```

If nothing is listening, start it and leave that terminal open:

```bash
cd ~/turn && . .venv/bin/activate
python3 -m signalling.server --bind 0.0.0.0 --port 8765
```

No new copy is required on EC2. Signalling already relays `start`,
`stop`, and `status`.

## 2. Robot PC — copy, then the terminal over SSH

From this PC:

```bash
cd /home/muhammadhassan/robots
rsync -av --exclude tests --exclude __pycache__ --exclude run packaging/ \
  muhammad-osama@100.120.193.52:~/teleops_hassan/packaging/
rsync -av mlink-transport/scripts/start_daemon.sh \
  muhammad-osama@100.120.193.52:~/teleops_hassan/mlink-transport/scripts/start_daemon.sh
```

On the robot PC, over the SSH session you already have. The gripper
is clear. Do not set `TELEOP_SUPERVISOR_DRY_RUN`.

```bash
cd ~/teleops_hassan
unset TELEOP_SUPERVISOR_DRY_RUN
PYTHONPATH=. /usr/bin/python3 -m packaging.robot_app \
  --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

Use `/usr/bin/python3`. If that Python cannot import `websockets`:

```bash
/usr/bin/python3 -m pip install --user websockets
```

Expect `status registered` and the inventory. With the devices plugged
in: `wlp0s20f3` is the default route, the follower `5B61033180` is the
default arm, `USB2.0_CAM1` is listed, and `camera_page` is
`http://100.120.193.52:8889/cam/`. If `camera_page` is empty, stop
here. Do not press Start.

Leave this terminal open.

## 3. This PC — operator window, Tailscale, Start

Do not set `TELEOP_SUPERVISOR_DRY_RUN`.

```bash
cd /home/muhammadhassan/robots
unset TELEOP_SUPERVISOR_DRY_RUN
PYTHONPATH=. python3 -m packaging.operator_app \
  --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

If the window does not appear, run that command with
`QT_QPA_PLATFORM=xcb` in front.

Log in with the ID and password printed by the robot terminal.

On the config page:

- Link: Tailscale
- Interface 1: `wlp0s20f3`
- Interface 2: None
- Arm: follower `5B61033180`
- Video: `USB2.0_CAM1`

Press Review. Pass: the window shows `config_ok`. The robot terminal
prints that config. Nothing has started yet.

Confirm the gripper is clear. Press Start.

The robot terminal prints status lines for mlink-edge, the camera, and
the arm, then `phase ready`. The operator window then starts mlink-op
and the operator container, and loads the console inside the window.
The page is `http://127.0.0.1:8090/?cam=` plus the camera page from
the inventory.

Pass: the HUD in that window says CONNECTED.

## 4. One motion, then torque off

With the gripper clear, tap `g` or `h` once. The gripper moves a small
amount.

Close the operator window. Do not press Stop before that close. Closing
stops the operator container and mlink-op. Heartbeats stop. Within
about a second the gripper torque goes off. Confirm by hand that the
gripper is slack.

The robot container can still be running after that close. That is the
watchdog check. After torque is off, stop the robot stack from the
robot SSH session:

```bash
cd ~/teleops_hassan
./teleoperation-prototype/scripts/stop_mlink.sh
if [ -f packaging/run/mlink-edge.pid ]; then
  kill "$(cat packaging/run/mlink-edge.pid)" || true
  rm -f packaging/run/mlink-edge.pid
fi
./video/so-arm/stop.sh
```

Type `q` in the robot terminal.

On this PC:

```bash
docker ps --filter name=ros2-teleop-operator-mlink --format '{{.Names}}' || true
ps -eo args | grep -E 'start_daemon.sh op|python3 -m op' | grep -v grep || true
```

Both print nothing from this check.

## 5. TURN, only when coturn and ICE signalling are already up

Do not start coturn for this step. If it is not already listening on
UDP 3478, skip this step.

Repeat sections 2 to 4 with Link set to TURN before Review. Interface 2
stays None. The follower serial and `USB2.0_CAM1` stay the same. Video
stays on the robot camera page.

The operator writes `packaging/run/ice-op.yaml` on this PC. The robot
writes `packaging/run/ice-edge.yaml`. `bind_ip` is that PC's
default-route IPv4, not a `100.` address. Do not print or commit either
file.

Same one tap of `g` or `h`, then close the window and confirm the
gripper torque goes off. Run the robot stop commands from section 4
again, and remove `packaging/run/mlink-op.pid` on this PC if it is
still there.
