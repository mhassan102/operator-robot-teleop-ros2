# P6 lab check

The robot terminal builds the start commands and answers `start` and
`stop`. This check sets `TELEOP_SUPERVISOR_DRY_RUN=1` on that process.
The reply is `ready` / `dry-run`, then `stopped` / `dry-run`. The
process does not spawn mlink, Docker, MediaMTX, or the arm. Leave the
gripper where it is. Do not open a serial port. Do not send `g` or `h`.

Keep the variable set to `1` for the whole check. The operator window
has no Start button yet, so this check uses a short client. Do not
open the operator window while the client is connected.

Link stays Tailscale. Interface 2 set to a NIC is refused at start.
Do not choose TURN. TURN would write `packaging/run/ice-edge.yaml`,
and that file can hold the TURN password. Do not print that file.
Do not commit it.

The password is printed in the robot terminal for this check. Do not
write it into git, a log, or this file.

## 1. This PC

```bash
cd /home/muhammadhassan/robots
PYTHONPATH=. python3 -m pytest -q packaging/tests
```

Pass: the packaging tests pass. That run does not start mlink, Docker,
the camera, or the arm.

Terminal A:

```bash
cd /home/muhammadhassan/robots/turn
PYTHONPATH=/home/muhammadhassan/robots python3 -m signalling.server --bind 127.0.0.1 --port 8765
```

Expect `signalling ws://127.0.0.1:8765`. Leave it open. Terminal B:

```bash
cd /home/muhammadhassan/robots
TELEOP_SUPERVISOR_DRY_RUN=1 PYTHONPATH=. python3 -m packaging.robot_app \
  --registry ws://127.0.0.1:8765
```

Expect `status registered` and this PC's inventory. Leave it open.
Copy the ID and password into the environment of Terminal C. Do not
echo the password.

```bash
cd /home/muhammadhassan/robots
export TELEOP_REGISTRY=ws://127.0.0.1:8765
export TELEOP_ID=
export TELEOP_PASSWORD=
python3 - << 'PY'
import asyncio, json, os
from websockets.asyncio.client import connect

URI = os.environ["TELEOP_REGISTRY"]
robot_id = os.environ["TELEOP_ID"]
password = os.environ["TELEOP_PASSWORD"]
ARM = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B61033180-if00"

def config(iface2):
    return {
        "v": 1, "type": "config", "link": "tailscale",
        "iface1": "wlp0s20f3", "iface2": iface2,
        "arm": ARM, "video": "/dev/video2",
    }

async def main():
    async with connect(URI) as ws:
        await ws.send(json.dumps({
            "v": 1, "type": "login",
            "robot_id": robot_id, "password": password,
        }))
        print("operator", await ws.recv())
        print("operator", await ws.recv())
        await ws.send(json.dumps(config("enx00e04c2c4570")))
        print("operator", await ws.recv())
        await ws.send(json.dumps({"v": 1, "type": "start"}))
        print("operator", await ws.recv())
        await ws.send(json.dumps(config(None)))
        print("operator", await ws.recv())
        await ws.send(json.dumps({"v": 1, "type": "start"}))
        print("operator", await ws.recv())
        await ws.send(json.dumps({"v": 1, "type": "stop"}))
        print("operator", await ws.recv())

asyncio.run(main())
PY
unset TELEOP_PASSWORD
```

Pass on this PC: the robot terminal shows `bad_config` (this machine
has no `wlp0s20f3` follower arm). Each start line is:

```text
Status  phase error  detail no config
```

The stop line is:

```text
Status  phase stopped  detail dry-run
```

`packaging/run/ice-edge.yaml` is absent. The gripper does not move.
No mlink daemon, MediaMTX, or `ros2-teleop-robot-mlink` container
starts.

Before typing `q`, compare process lists. Run this in another terminal,
then type `q` in Terminal B and Ctrl-C Terminal A:

```bash
ps -eo args | grep -E 'start_daemon.sh|start_robot_mlink.sh|mediamtx|gst-publish' | grep -v grep || true
docker ps --filter name=ros2-teleop-robot-mlink --format '{{.Names}}' || true
```

The first command prints nothing from this check. The second prints
nothing.

Copy the tree onto the robot PC:

```bash
cd /home/muhammadhassan/robots
rsync -av --exclude tests --exclude __pycache__ --exclude run packaging/ \
  muhammad-osama@100.120.193.52:~/teleops_hassan/packaging/
rsync -av mlink-transport/scripts/start_daemon.sh \
  muhammad-osama@100.120.193.52:~/teleops_hassan/mlink-transport/scripts/start_daemon.sh
```

EC2 does not need a new copy. Signalling already relays `start`,
`stop`, and `status`.

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
TELEOP_SUPERVISOR_DRY_RUN=1 PYTHONPATH=. /usr/bin/python3 -m packaging.robot_app \
  --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

Use `/usr/bin/python3` so the Miniforge `(base)` prompt is not the
interpreter. If that Python cannot import `websockets`:

```bash
/usr/bin/python3 -m pip install --user websockets
```

Expect `status registered` and the inventory. With the devices
plugged in: `wlp0s20f3` is the default route, `enx00e04c2c4570` is
local-only, the follower `5B61033180` is marked default, and
`USB2.0_CAM1` is listed. Leave this terminal open. Do not unset
`TELEOP_SUPERVISOR_DRY_RUN`. Do not start mlink, Docker, the camera,
or the arm from this shell.

## 4. This PC — dry-run client

Same client as section 1, with the EC2 registry and the ID and
password from the robot terminal:

```bash
cd /home/muhammadhassan/robots
export TELEOP_REGISTRY=ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
export TELEOP_ID=
export TELEOP_PASSWORD=
python3 - << 'PY'
import asyncio, json, os
from websockets.asyncio.client import connect

URI = os.environ["TELEOP_REGISTRY"]
robot_id = os.environ["TELEOP_ID"]
password = os.environ["TELEOP_PASSWORD"]
ARM = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B61033180-if00"

def config(iface2):
    return {
        "v": 1, "type": "config", "link": "tailscale",
        "iface1": "wlp0s20f3", "iface2": iface2,
        "arm": ARM, "video": "/dev/video2",
    }

async def main():
    async with connect(URI) as ws:
        await ws.send(json.dumps({
            "v": 1, "type": "login",
            "robot_id": robot_id, "password": password,
        }))
        print("operator", await ws.recv())
        print("operator", await ws.recv())
        await ws.send(json.dumps(config("enx00e04c2c4570")))
        print("operator", await ws.recv())
        await ws.send(json.dumps({"v": 1, "type": "start"}))
        print("operator", await ws.recv())
        await ws.send(json.dumps(config(None)))
        print("operator", await ws.recv())
        await ws.send(json.dumps({"v": 1, "type": "start"}))
        print("operator", await ws.recv())
        await ws.send(json.dumps({"v": 1, "type": "stop"}))
        print("operator", await ws.recv())

asyncio.run(main())
PY
unset TELEOP_PASSWORD
```

Pass: the client prints `logged_in` and the robot inventory. The
first config reply is `config_ok` with Interface 2 set. The robot
terminal prints that line, then:

```text
Status  phase error  detail second interface is not used in this version; set Interface 2 to None
```

The client prints the same status. The second config reply is
`config_ok` with `iface2 none`. The robot terminal then prints:

```text
Status  phase ready  detail dry-run
ID <9 digits>  Password <8 characters>  status ready
```

Stop prints:

```text
Status  phase stopped  detail dry-run
```

The video path on the config line is the `USB2.0_CAM1` node. The
gripper does not move. No mlink daemon, MediaMTX, or
`ros2-teleop-robot-mlink` container starts. `packaging/run/` on the
robot stays absent.

On the robot PC, after the client exits:

```bash
ps -eo args | grep -E 'start_daemon.sh|start_robot_mlink.sh|mediamtx|gst-publish' | grep -v grep || true
docker ps --filter name=ros2-teleop-robot-mlink --format '{{.Names}}' || true
```

Both print nothing from this check. Type `q` in the robot terminal.
Do not run `start_robot_mlink.sh`, `video/so-arm/start.sh`, or
`start_daemon.sh` without the dry-run variable on the robot app.
Do not send `g` or `h`.
