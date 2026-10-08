# Lab check

Signalling on EC2 is stopped. The robot app on the robot PC is
stopped. Coturn stays up. The gripper is clear of the table and of
hands before Start. One tap of `g` or `h` after the HUD says
CONNECTED. Do not hold the key. Do not press `n`. Do not write the
password into git, a log, or this file.

## 1. EC2

From this PC:

```bash
cd /home/muhammadhassan/robots
rsync -av turn/signalling/server.py \
  ec2-user@3.227.234.95:~/turn/signalling/server.py
rsync -av --exclude tests --exclude __pycache__ --exclude run packaging/ \
  ec2-user@3.227.234.95:~/turn/packaging/
```

On EC2:

```bash
cd ~/turn && . .venv/bin/activate
python3 -m signalling.server --bind 0.0.0.0 --port 8765
```

Pass: the line `signalling ws://0.0.0.0:8765`. Leave that terminal open.

## 2. Robot PC

```bash
cd /home/muhammad-osama
unset TELEOP_SUPERVISOR_DRY_RUN
teleop-robot \
  --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

Pass: `status registered` and the inventory. `wlp0s20f3` is the
default route, follower `5B61033180` is the default arm,
`USB2.0_CAM1` is listed, and `camera_page` is
`http://100.120.193.52:8889/cam/`. If `camera_page` is empty, stop
here. Leave this terminal open and read the ID and password from it.

## 3. This PC

```bash
cd /home/muhammadhassan
unset TELEOP_SUPERVISOR_DRY_RUN
teleop-operator \
  --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

Log in with the ID and password from the robot terminal.

- Link: Tailscale
- Interface 1: `wlp0s20f3`
- Interface 2: None
- Arm: follower `5B61033180`
- Video: `USB2.0_CAM1`

Press Review. Confirm the gripper is clear. Press Start.

Pass: the HUD says CONNECTED. Tap `g` or `h` once. Press Stop. The
window returns to the config page and the robot terminal shows
`phase stopped`.
