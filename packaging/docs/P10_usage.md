# P10 lab check

Launching the operator opens a desktop window on this PC titled
Teleop operator. Login and config are that window. mlink starts only
when you press Start. Stop and Logout leave the operator container
up while the window stays open. Closing the window, or pressing
Quit, stops the robot stack, removes the operator container, and
the launch process exits.

This check starts the gripper. The person at the robot watches the
arm. The gripper is clear of the table and of hands before each
Start. The first motion is one tap of `g` or `h` after the HUD says
CONNECTED. Do not hold the key. Do not send any other motion key.

Leave `TELEOP_SUPERVISOR_DRY_RUN` unset on both PCs. When that
variable is `1`, the robot answers `dry-run` and Start does not
run mlink. Do not restart the EC2 signalling process.

The password is printed in the robot terminal for this check. Do not
write it into git, a log, or this file. Do not print
`packaging/run/ice-op.yaml` or `packaging/run/ice-edge.yaml`. Do not
press `n` in the robot terminal. Logout must not print a new password.

## 0. This PC — pytest only

```bash
cd /home/muhammadhassan/robots
PYTHONPATH=. python3 -m pytest -q packaging/tests
```

Pass: the packaging tests pass. That run does not start mlink, Docker,
the camera, the console, or the arm, and it does not open a serial
port.

## 1. EC2 — confirm signalling, do not restart it

P10 does not change registry messages. Signalling is already on TCP
8765. Do not rsync. Do not restart the process. Do not start a second
listener. Coturn stays as it is.

On EC2:

```bash
ss -ltnp | grep 8765
```

Pass: one listener on TCP 8765. Leave that process running.

## 2. Robot PC — the terminal you already have

The robot app is unchanged. If that terminal is already running and
shows an ID and a password, leave it. Do not press `n`. Do not set
`TELEOP_SUPERVISOR_DRY_RUN`.

If it is not running, over the SSH session you already have, with the
gripper clear. From the repo checkout:

```bash
cd ~/teleops_hassan
unset TELEOP_SUPERVISOR_DRY_RUN
PYTHONPATH=. /usr/bin/python3 -m packaging.robot_app \
  --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

Or, after `sudo dpkg -i` of `teleop-robot_1.0.0_amd64.deb` on that PC:

```bash
unset TELEOP_SUPERVISOR_DRY_RUN
teleop-robot \
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

Leave this terminal open. Read the ID and password from it.

## 3. This PC — desktop window, Tailscale, Start

If the previous operator window is still open and the gripper is
stiff, press Stop in that window first. Then close the window. That
older launch leaves the operator container up after the window
closes, so bring the container down before the new launch. This down
does not start mlink.

```bash
cd /home/muhammadhassan/robots/teleoperation-prototype
docker compose -f compose.operator-mlink.yaml down --remove-orphans
```

Do not set `TELEOP_SUPERVISOR_DRY_RUN`. Do not run
`start_operator_mlink.sh`.

From this repo, the launch command is:

```bash
cd /home/muhammadhassan/robots
unset TELEOP_SUPERVISOR_DRY_RUN
PYTHONPATH=. python3 -m packaging.operator_app \
  --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

That is the same program as the installed command. On the operator
PC, after `sudo dpkg -i packaging/dist/teleop-operator_1.0.0_amd64.deb`:

```bash
unset TELEOP_SUPERVISOR_DRY_RUN
teleop-operator \
  --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

Install that deb only on this PC. The robot PC gets the other deb,
then:

```bash
teleop-robot \
  --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

The two packages conflict, so do not install both on one machine.

A desktop window titled Teleop operator opens. The pages are the
console at `http://127.0.0.1:8090/`. Leave this terminal open. It
exits when you close that window or press Quit.

Before you log in:

```bash
docker ps --filter name=ros2-teleop-operator-mlink --format '{{.Names}}'
ss -ltnp | grep 8090
ss -ltnp | grep 8091
if [ -f /home/muhammadhassan/robots/packaging/run/mlink-op.pid ]; then echo "op pid still present"; else echo "op pid clear"; fi
ss -ulnp | grep -E '5501|5502' || echo "mlink udp clear"
```

Pass: the container name is listed, TCP 8090 and TCP 8091 are
listening, `op pid clear`, and `mlink udp clear`. 8091 is the host
helper. The window does not call it. Nothing is listening on TCP
8765 on this PC.

Log in with the ID and password printed by the robot terminal. A
wrong password says `Wrong password.` A robot that is not registered
says `That robot is offline.` The password field is not stored in
the page.

Refresh the config page after it appears. The robot terminal does
not show a second login.

On the config page:

- Link: Tailscale
- Interface 1: `wlp0s20f3`
- Interface 2: None
- Arm: follower `5B61033180`
- Video: `USB2.0_CAM1`

The line under the link choice says TURN changes gripper control and
video stays on the robot camera page. Interface 2 other than None
refuses Start. Do not change it.

Press Review. Pass: the page shows `config_ok`. The robot terminal
prints that config. The operator container was already up. mlink-op
is not running yet.

Confirm the gripper is clear. Press Start.

The robot terminal prints status lines for mlink-edge, the camera,
and the arm, then `phase ready`. This PC then starts mlink-op only.
The window goes to `/operate?cam=` plus the camera page. Stop,
Logout, and Quit stay on that page.

Pass: the HUD says CONNECTED.

## 4. One motion, then Stop

With the gripper clear, tap `g` or `h` once. The gripper moves a
small amount.

Press Stop. Do not press Logout. Do not press Quit. Do not close
the window.

Stop brings mlink-op down first. Heartbeats end. Within about a
second the gripper torque goes off. The robot then stops its
container, mlink-edge, and the camera. The window leaves the drive
page and shows the config page again. Start is enabled. The login
is the same one. Do not type the ID again. The operator container
stays up.

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

Pass: the robot `docker ps` line is empty, the operator container
name is still listed, the pid lines say `clear`, and the gripper is
slack. The robot terminal shows `phase stopped`. If the status line
says the robot did not confirm, stop here and do not press Start.

## 5. Start again, one motion

Do not log in again. Do not press Review unless you changed a
dropdown. The dropdowns are unchanged.

Confirm the gripper is clear. Press Start.

Pass: the HUD says CONNECTED again. Stop, Logout, and Quit are on
the drive page. The operator container was up the whole time.

Tap `g` or `h` once. The gripper moves a small amount.

If a dropdown was changed before this Start, the page asks for
Review and does not start. Set the dropdowns back, or press Review,
then Start. Interface 2 stays None.

## 6. Logout, window still open

Press Stop if the drive page is still up. Press Logout. Do not press
Quit. Do not close the window.

Pass:

- The window returns to the login page. Start is not on that page.
- The robot terminal still shows the same ID and the same password.
  It does not print a new password.
- On the robot PC the checks from section 4 still say the container,
  edge pid, and camera pids are clear. The gripper is slack.
- On this PC the operator container is still up and `mlink-op.pid`
  is clear.

Log in again with that same ID and password. Pass: the config page
appears. Do not press Start. Press Logout. The login page returns.
The robot terminal still shows the same ID and password. The
operator container is still up.

## 7. Close the window

Close the Teleop operator window. Pressing Quit on the login page
does the same clean close, so you only need one of them.

Pass: the launch command returns. The gripper stays slack. The
operator container is gone.

```bash
docker ps --filter name=ros2-teleop-operator-mlink --format '{{.Names}}' || true
ss -ltnp | grep -E '8090|8091' || echo "console ports clear"
```

Pass: `docker ps` prints nothing and the port line says `console
ports clear`. The robot terminal is still running and still shows
the same ID and password. Closing the window is not a new robot
password.

If you close the window while the HUD says CONNECTED, the same close
stops mlink, stops the robot stack, and then removes the operator
container. The gripper torque goes off. Do that only when the
gripper is clear.
