# P9 lab check

Stop and Logout stay on screen while the console is showing. Stop
brings the operator stack down first, then the robot stack, and
returns to the config page. Start can run again on that same login
with the config already accepted. Logout does that stop, detaches
the operator, and returns to the login page. The robot terminal keeps
its ID and password. Closing the operator window stops both sides,
then exits. Ctrl-C or SIGTERM on the robot process runs that same
robot stop, then the process exits.

This check starts the gripper. The person at the robot watches the
arm. The gripper is clear of the table and of hands before each
Start. The first motion is one tap of `g` or `h` after the HUD says
CONNECTED. Do not hold the key. Do not send any other motion key.

Leave `TELEOP_SUPERVISOR_DRY_RUN` unset on both PCs. When that
variable is `1`, the robot answers `dry-run` and the operator window
does not start mlink, Docker, or the console.

The password is printed in the robot terminal for this check. Do not
write it into git, a log, or this file. Do not print
`packaging/run/ice-op.yaml` or `packaging/run/ice-edge.yaml`. Do not
press `n` in the robot terminal. Logout must not print a new password.

## 0. This PC — pytest only

```bash
cd /home/muhammadhassan/robots
QT_QPA_PLATFORM=offscreen PYTHONPATH=. python3 -m pytest -q packaging/tests
```

Pass: the packaging tests pass. That run does not start mlink, Docker,
the camera, the console, or the arm, and it does not open a serial
port.

## 1. EC2 — copy the registry and restart signalling

Logout is handled by the signalling process. Copy the packaging tree
and restart the process on TCP 8765. Coturn stays as it is. Do not
start a second listener. Restarting signalling drops any login that
is using that port, so do this before the robot terminal and the
operator window connect.

From this PC:

```bash
cd /home/muhammadhassan/robots
rsync -av --exclude tests --exclude __pycache__ --exclude run packaging/ \
  ec2-user@3.227.234.95:~/turn/packaging/
```

On EC2, stop the process `ss -ltnp | grep 8765` shows, then:

```bash
cd ~/turn && . .venv/bin/activate
python3 -m signalling.server --bind 0.0.0.0 --port 8765
```

Expect `signalling ws://0.0.0.0:8765`. Leave that terminal open.

## 2. Robot PC — copy, then the terminal over SSH

From this PC:

```bash
cd /home/muhammadhassan/robots
rsync -av --exclude tests --exclude __pycache__ --exclude run packaging/ \
  muhammad-osama@100.120.193.52:~/teleops_hassan/packaging/
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

Leave this terminal open. Read the ID and password from it. Do not
press `n`.

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
Stop and Logout stay visible above that console.

Pass: the HUD in that window says CONNECTED.

## 4. One motion, then Stop

With the gripper clear, tap `g` or `h` once. The gripper moves a small
amount.

Press Stop. Do not close the window. Do not press Logout.

Stop brings the operator container and mlink-op down first. Heartbeats
end. Within about a second the gripper torque goes off. The robot then
stops its container, mlink-edge, and the camera. The window leaves the
console and shows the config page again. Start is enabled. The login
is the same one. Do not type the ID again.

On the robot PC:

```bash
cd ~/teleops_hassan
docker ps --filter name=ros2-teleop-robot-mlink --format '{{.Names}}' || true
if [ -f packaging/run/mlink-edge.pid ]; then echo "edge pid still present"; else echo "edge pid clear"; fi
if [ -f video/so-arm/run/gst.pid ] || [ -f video/so-arm/run/mediamtx.pid ]; then echo "camera pid still present"; else echo "camera pid clear"; fi
```

On this PC:

```bash
cd /home/muhammadhassan/robots
docker ps --filter name=ros2-teleop-operator-mlink --format '{{.Names}}' || true
if [ -f packaging/run/mlink-op.pid ]; then echo "op pid still present"; else echo "op pid clear"; fi
```

Pass: both `docker ps` lines are empty, the three pid lines say
`clear`, and the gripper is slack. The robot terminal shows
`phase stopped`. If the status line says the robot did not confirm,
stop here and do not press Start.

## 5. Start again, one motion

Do not log in again. Do not press Review. The dropdowns are unchanged.

Confirm the gripper is clear. Press Start.

Pass: the HUD says CONNECTED again. The page is still inside the
operator window. Stop and Logout are still above it.

Tap `g` or `h` once. The gripper moves a small amount.

If a dropdown was changed before this Start, the window asks for
Review and does not start. Set the dropdowns back, or press Review,
then Start. Interface 2 stays None.

## 6. Logout

Press Logout. Do not close the window first.

Pass:

- The window returns to the login page. Start is not on that page.
- The robot terminal still shows the same ID and the same password.
  It does not print a new password.
- On the robot PC the checks from section 4 still say the container,
  edge pid, and camera pids are clear. The gripper is slack.
- On this PC the operator container and `mlink-op.pid` are clear.

Log in again with that same ID and password. Pass: the hostname
appears, then the config page. Do not press Start. Press Logout.
The login page returns. The robot terminal still shows the same ID
and password.

## 7. Closing the window

Log in again. Press Review if the window does not already show
`config_ok`. Confirm the gripper is clear. Press Start. Wait until
the HUD says CONNECTED. Do not press `g` or `h`. The gripper can
stiffen when the HUD connects.

Close the operator window. Do not press Logout.

Pass: the operator process exits. Within about a second the gripper
is slack. On the robot PC the container, edge pid, and camera pids
from section 4 are clear. The robot terminal is still running and
still shows the same ID and password. This close is not a logout:
starting the operator window again and logging in with that same
password works. Do not press Start after that login. Press Logout.

## 8. Ctrl-C on the robot

Log in, Review if needed, and press Start with the gripper clear.
Wait until the HUD says CONNECTED. Do not press `g` or `h`.

Press Ctrl-C in the robot terminal.

Pass: that process exits. The robot container, mlink-edge, and the
camera are down (section 4 checks). The gripper is slack. The
operator window can say that the robot did not confirm; the robot
process has already exited, so it may not send `stopped`. Close the
operator window.

Starting `python3 -m packaging.robot_app` again prints a new ID and
a new password. That is a new process. Logout in section 6 does not
do that.

`q` and Enter in the robot terminal also runs the robot stop and
then exits. SIGTERM does the same.
