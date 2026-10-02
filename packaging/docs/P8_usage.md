# P8 install

Two amd64 packages, for Ubuntu 22.04 and 24.04. `dpkg -i` is how they
install. Each one unpacks files under `/opt/teleop` and adds one
command. They are not one compiled binary.

| Package | Where | Command |
| --- | --- | --- |
| `teleop-operator` | This PC (Ubuntu 22.04) | `teleop-operator` opens the Qt window |
| `teleop-robot` | Robot PC (Ubuntu 24.04) | `teleop-robot` opens the terminal |

Installing either package does not start the arm, mlink, or the
camera. The image `ros2-teleop-poc:humble` is not inside the deb. It
stays on the machine. `postinst` only checks that `docker` and that
image exist. If either is missing it prints one line and exits
non-zero.

Do not type `python3 -m packaging.operator_app` or
`python3 -m packaging.robot_app` after install. Do not `git add`
`video/bin/mediamtx`. Do not copy `turn/config/local_op.yaml`,
`turn/config/local_edge.yaml`, or `turn/scripts/coturn.env` into the
deb. Those files are not in the packages.

The operator package depends on `python3`, `python3-pyqt5`,
`python3-pyqt5.qtwebengine`, and `python3-yaml`. The robot package
depends on `python3` and `python3-yaml`. It does not depend on PyQt.
`websockets` is vendored under `/opt/teleop/vendor` because the Ubuntu
`python3-websockets` package does not provide `websockets.asyncio`.
Nothing is pip-installed during `dpkg -i`.

Install `teleop-operator` on this PC and `teleop-robot` on the robot
PC. Do not install both on the same PC. They overlap under
`/opt/teleop` and each package conflicts with the other.

## 0. This PC — build and inspect

```bash
cd /home/muhammadhassan/robots
./packaging/debian/build.sh
dpkg-deb -I packaging/dist/teleop-operator_*_amd64.deb
dpkg-deb -I packaging/dist/teleop-robot_*_amd64.deb
dpkg-deb -c packaging/dist/teleop-operator_*_amd64.deb
dpkg-deb -c packaging/dist/teleop-robot_*_amd64.deb
```

Pass:

- `build.sh` exits 0 and writes
  `packaging/dist/teleop-operator_1.0.0_amd64.deb` and
  `packaging/dist/teleop-robot_1.0.0_amd64.deb`.
- Operator `Depends` lists `python3-pyqt5` and
  `python3-pyqt5.qtwebengine`.
- Robot `Depends` lists `python3` and does not list PyQt.
- `dpkg-deb -c` shows `./usr/bin/teleop-operator` only in the operator
  package, and `./usr/share/applications/teleop-operator.desktop` only
  there.
- `dpkg-deb -c` shows `./usr/bin/teleop-robot` only in the robot
  package. That package has no desktop file.
- Neither listing contains `local_op.yaml`, `local_edge.yaml`,
  `coturn.env`, or `ros2_ws/log`.
- Both listings contain
  `./opt/teleop/teleoperation-prototype/ros2_ws/src`,
  `ros2_ws/install/setup.bash`, and `ros2_ws/build`.
- The robot listing contains `./opt/teleop/video/so-arm/bin/mediamtx`
  when `video/bin/mediamtx` was on the build machine. The operator
  listing does not contain `mediamtx`.

`build.sh` does not install the packages.

## 1. This PC — install the operator package

Qt packages, if they are not already installed:

```bash
sudo apt-get install python3 python3-pyqt5 python3-pyqt5.qtwebengine python3-yaml
```

Then:

```bash
cd /home/muhammadhassan/robots
sudo dpkg -i packaging/dist/teleop-operator_1.0.0_amd64.deb
```

If `python3-pyqt5.qtwebengine` is not installed, `dpkg` stops before
the program is configured and names that package:

```text
dpkg: dependency problems prevent configuration of teleop-operator:
 teleop-operator depends on python3-pyqt5.qtwebengine; however:
  Package python3-pyqt5.qtwebengine is not installed.
```

Install the package it names, then run `dpkg -i` again.

If `docker` is missing, configuration fails with:

```text
teleop-operator: docker is missing
```

If `docker` is present and the image is not, configuration fails with:

```text
teleop-operator: image ros2-teleop-poc:humble is missing
```

The deb does not download or contain that image. When the missing
piece is already on this PC, run:

```bash
sudo dpkg --configure teleop-operator
```

The ROS workspace is already in the package at
`/opt/teleop/teleoperation-prototype/ros2_ws`. No link to a checkout
is required. Opening the window does not use it. Start does.

Open the window:

```bash
unset TELEOP_SUPERVISOR_DRY_RUN
teleop-operator --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

Pass: the Qt login window opens. Close it. The arm does not move.
If the window does not appear, run the same command with
`QT_QPA_PLATFORM=xcb` in front.

The desktop launcher `Teleop Operator` runs the same command with the
default registry `ws://127.0.0.1:8765`. The lab uses the command above
so the window talks to EC2.

## 2. Robot PC — install the robot package

Copy the deb from this PC. Do not copy the ROS image.

```bash
cd /home/muhammadhassan/robots
scp packaging/dist/teleop-robot_1.0.0_amd64.deb \
  muhammad-osama@100.120.193.52:~/teleop-robot_1.0.0_amd64.deb
```

On the robot PC, in the SSH session you already have. Do not install
PyQt for this package.

```bash
sudo apt-get install python3 python3-yaml
sudo dpkg -i ~/teleop-robot_1.0.0_amd64.deb
```

Pass: `dpkg` finishes and does not ask for a PyQt package. The same
two failure lines as section 1 apply, with the name `teleop-robot`:

```text
teleop-robot: docker is missing
teleop-robot: image ros2-teleop-poc:humble is missing
```

The image on the robot stays the one already loaded there. This
package does not start the arm while it installs. The robot package
carries the same `ros2_ws` under `/opt/teleop/teleoperation-prototype`.
No link to `~/teleops_hassan` is required.

Open the terminal program. Use the system Python that the command
already selects. The gripper stays where it is.

```bash
unset TELEOP_SUPERVISOR_DRY_RUN
teleop-robot --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

Pass: the terminal prints a 9-digit ID and `New password:`. Type a
password, then type it again. The characters stay hidden. The password
is not printed. Status becomes `registered`. Type `q` to quit. The
arm, camera, and mlink stay stopped.

MediaMTX for the camera is already inside the robot package at
`/opt/teleop/video/so-arm/bin/mediamtx` when the build machine had
`video/bin/mediamtx`. No separate copy is required on the robot.

## 3. EC2

Do not install either deb on EC2. Amazon Linux is not a target.
Signalling on TCP 8765 stays as it is. This install does not change
the registry, so do not rsync or restart it. Coturn stays up.

## 4. Start, only when the gripper is clear

Opening the window and the terminal does not move the arm. Start
still does. The person at the robot watches the arm. The gripper is
clear of the table and of hands. The first motion is one tap of `g`
or `h` after the HUD says CONNECTED.

Log in from the operator window with the ID on the robot terminal and
the password typed there. Review, then Start, is the same check as
`packaging/docs/P9_usage.md`, with the two commands above instead of
the repo `python3 -m` lines. Leave Interface 2 on None.

TURN still reads `turn/config/local_op.yaml` on this PC and
`turn/config/local_edge.yaml` on the robot. Those files are not in
the debs. For a TURN link, copy each one into place and do not print
it:

```bash
sudo install -m 600 /home/muhammadhassan/robots/turn/config/local_op.yaml \
  /opt/teleop/turn/config/local_op.yaml
```

On the robot, the same `install` from the copy already in
`~/teleops_hassan/turn/config/local_edge.yaml` to
`/opt/teleop/turn/config/local_edge.yaml`. Tailscale does not need
either file.
