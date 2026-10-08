# Lab check

Signalling on EC2 is stopped. The robot app on the robot PC is
stopped. Coturn stays up. The gripper is clear of the table and of
hands before Start. One tap of `g` or `h` after the HUD says
CONNECTED. Do not hold the key. Do not press `n`. Do not write the
password into git, a log, or this file.

## 1. Robot PC

From this PC:

```bash
cd /home/muhammadhassan/robots
./packaging/debian/build.sh
rsync -av packaging/dist/teleop-robot_1.0.0_amd64.deb \
  muhammad-osama@100.120.193.52:~/teleop-robot_1.0.0_amd64.deb
```

On the robot PC:

```bash
sudo dpkg -i ~/teleop-robot_1.0.0_amd64.deb
cd /home/muhammad-osama
unset TELEOP_SUPERVISOR_DRY_RUN
teleop-robot \
  --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

Leave this terminal open. It prints the id and does not ask for a
password. Do not copy the token. After signalling is up, pass is
`status registered` and the inventory. `wlp0s20f3` is the default
route, follower `5B61033180` is the default arm, `USB2.0_CAM1` is
listed, and `camera_page` is `http://100.120.193.52:8889/cam/`. If
`camera_page` is empty, stop here. Read the id from this terminal.
The fleet table shows the same id. Login is not the robot id.

## 2. EC2

On EC2:

```bash
cd ~/turn && . .venv/bin/activate
python3 -m signalling.server --bind 0.0.0.0 --port 8765
```

Pass: the line `signalling ws://0.0.0.0:8765`. Leave that terminal
open. The robot terminal then shows `status registered`.

If the operator account is not already there, open another terminal
on EC2. Use the same venv. Do not set `TELEOP_REGISTRY_DB`.

```bash
cd ~/turn && . .venv/bin/activate
python3 -m packaging.registry.admin add-operator lab
```

It asks once for a password of at least 8 characters and prints
`lab`. If it prints `operator exists`, leave the account.

## 3. This PC

```bash
cd /home/muhammadhassan/robots
./packaging/debian/build.sh
sudo dpkg -i packaging/dist/teleop-operator_1.0.0_amd64.deb
cd /home/muhammadhassan
unset TELEOP_SUPERVISOR_DRY_RUN
teleop-operator \
  --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

Log in with the operator username and password (`lab` and the
password just set).

Approve the robot, then Operate.

- Link: Tailscale
- Interface 1: `wlp0s20f3`
- Interface 2: None
- Arm: follower `5B61033180`
- Video: `USB2.0_CAM1`

Press Review. Confirm the gripper is clear. Press Start.

Pass: the HUD says CONNECTED. Tap `g` or `h` once. Press Stop. The
window returns to the config page and the robot terminal shows
`phase stopped`.
