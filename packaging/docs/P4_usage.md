# P4 lab check

Log in, then review Link, Interface 1, Interface 2, Arm, and Video.
Review sends that choice. The robot terminal prints it and replies.
Interface 2 may be set. The reply is still `config_ok` when the arm
and video are in the inventory. Review does not start mlink, Docker,
the camera, or the arm. Leave the gripper where it is. Do not open a
serial port. Do not send `g` or `h`.

The password is printed in the robot terminal for this check. Do not
write it into git, a log, or this file.

## 1. This PC — local window, then copy

`websockets` must import. If it does not:

```bash
/usr/bin/python3 -m pip install --user websockets
```

The window needs PyQt5 from apt:

```bash
sudo apt install python3-pyqt5
```

Terminal A:

```bash
cd /home/muhammadhassan/robots/turn
PYTHONPATH=/home/muhammadhassan/robots python3 -m signalling.server --bind 127.0.0.1 --port 8765
```

Expect `signalling ws://127.0.0.1:8765`. Leave it open. Terminal B:

```bash
cd /home/muhammadhassan/robots
PYTHONPATH=. python3 -m packaging.robot_app --registry ws://127.0.0.1:8765
```

Expect `ID <9 digits>  Password <8 characters>  status registered`
and this PC's inventory. Terminal C:

```bash
cd /home/muhammadhassan/robots
PYTHONPATH=. python3 -m packaging.operator_app --registry ws://127.0.0.1:8765
```

If the window does not appear, run that command with
`QT_QPA_PLATFORM=xcb` in front.

Type the ID and password. The window shows this PC's hostname and
`Waiting for the robot.` Then the config page:

- Link: Tailscale is selected. Under the radios:
  `TURN changes gripper control. Video stays on the robot camera page.`
- Interface 1: `wlo1`.
- Interface 2: `None`. The list also includes `wlo1`, and
  `enx00e04c681cc3 (local-only)` when that USB ethernet is present.
- Arm: empty. This PC has no `/dev/serial/by-id` node.
- Video: the laptop cameras are listed. None is selected, because
  none is named `USB2.0_CAM1`.
- A read-only line `Operator network: wlo1`. It is not a dropdown.
  On this local check it matches Interface 1, because the robot
  terminal is running on this PC.

There is no Start button. Press Review.

Pass: the window shows `unknown arm`. Terminal B prints a `Config`
line with `iface1 wlo1`, `iface2 none`, `arm -`, and
`bad_config  unknown arm`, then `status bad_config`. The arm, camera,
and mlink stay stopped.

Set Interface 2 to the local-only NIC and press Review again. Pass:
the new `Config` line names that NIC, and the detail is still
`unknown arm`. Nothing is bonded and nothing starts.

Type `q` in the robot terminal, and Ctrl-C Terminal A.

Copy the tree onto the robot PC:

```bash
cd /home/muhammadhassan/robots
rsync -av --exclude tests --exclude __pycache__ packaging/ muhammad-osama@100.120.193.52:~/teleops_hassan/packaging/
```

EC2 does not need a new copy. Signalling already relays `config`.

## 2. EC2 — leave signalling up

Confirm the process is listening:

```bash
ss -ltnp | grep 8765
```

Expect the signalling process on TCP 8765. Leave it. Do not restart
it. Coturn stays as it is. Do not start a second listener.

If nothing is listening, start it and leave that terminal open:

```bash
cd ~/turn && . .venv/bin/activate
python3 -m signalling.server --bind 0.0.0.0 --port 8765
```

## 3. Robot PC — terminal over SSH, and leave it open

```bash
cd ~/teleops_hassan
PYTHONPATH=. /usr/bin/python3 -m packaging.robot_app \
  --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

Use `/usr/bin/python3` so the Miniforge `(base)` prompt is not the
interpreter. If that Python cannot import `websockets`:

```bash
/usr/bin/python3 -m pip install --user websockets
```

Expect `status registered` and the inventory. With the devices
plugged in: `wlp0s20f3` is the default route, `enx00e04c2c4570` is
local-only, the follower `5B61033180` is marked default, the leader
`5B3E090040` is not, and `USB2.0_CAM1` is listed. Metadata names are
absent. Leave this terminal open. Do not start the operator window
here. Do not start mlink, Docker, the camera, or the arm.

## 4. This PC — operator window

```bash
cd /home/muhammadhassan/robots
PYTHONPATH=. python3 -m packaging.operator_app \
  --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

Pass: the window shows `AUTOOS-DEV-MUHAMMADOSAMA`, then the config page.

- Tailscale is selected, with the TURN help line under the radios.
- Interface 1 is `wlp0s20f3`.
- Interface 2 is `None`, and the list includes
  `enx00e04c2c4570 (local-only)`.
- Arm selects the follower `…5B61033180…` and shows its `ttyACM`.
  The leader is in the list and is not selected.
- Video selects `USB2.0_CAM1`.
- `Operator network: wlo1`. That line stays this PC's NIC. It is not
  sent as Interface 1 or Interface 2.

Press Review with Interface 2 left at None.

Pass: the window shows `config_ok`. The robot terminal prints:

```text
Config  link tailscale  iface1 wlp0s20f3  iface2 none  arm /dev/serial/by-id/usb-1a86_USB_Single_Serial_5B61033180-if00  video <USB2.0_CAM1 path>  config_ok
ID <9 digits>  Password <8 characters>  status config_ok
```

The video path is the `USB2.0_CAM1` node from the inventory (`/dev/video2`
when that node is the capture device). The gripper does not move.
No mlink, camera, or container starts.

Set Interface 2 to `enx00e04c2c4570 (local-only)` and press Review again.

Pass: the window shows `config_ok`. The robot line has
`iface2 enx00e04c2c4570` and `config_ok`. That NIC is not bonded.
The arm stays still.

Close the operator window, then type `q` in the robot terminal.
