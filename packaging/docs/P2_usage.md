# P2 lab check

Run the robot program in a terminal, read the ID and password it
prints, and log in from this PC. It registers on its own and sends
inventory when an operator attaches. No display is required, so the
same command works over SSH. Leave the arm, camera, and mlink stopped.
Coturn stays up. The operator app is unchanged and is still the UI.

The password is printed in the robot terminal for this check. Do not
write it into git, a log, or this file.

## 1. This PC — local terminal, then copy

`websockets` must import. If it does not:

```bash
/usr/bin/python3 -m pip install --user websockets
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

Expect a line `ID <9 digits>  Password <8 characters>  status registered`.
The inventory list is this PC's NICs (loopback, tailscale0, docker
bridges, and veth hidden). Terminal C runs the login in step 4 with
`URI` set to `ws://127.0.0.1:8765`. The robot terminal prints
`status operator_attached` and the inventory again. The operator
prints that inventory. Type `q` or Ctrl-C in the robot terminal, and
Ctrl-C Terminal A, before the robot-PC check.

Copy onto EC2 and the robot PC:

```bash
cd /home/muhammadhassan/robots
rsync -av turn/signalling/server.py ec2-user@3.227.234.95:~/turn/signalling/server.py
rsync -av --exclude tests --exclude __pycache__ packaging/ ec2-user@3.227.234.95:~/turn/packaging/
rsync -av --exclude tests --exclude __pycache__ packaging/ muhammad-osama@100.120.193.52:~/teleops_hassan/packaging/
```

## 2. EC2 — restart signalling

Stop the process `ss -ltnp | grep 8765` shows, then:

```bash
cd ~/turn && . .venv/bin/activate
python3 -m signalling.server --bind 0.0.0.0 --port 8765
```

Expect `signalling ws://0.0.0.0:8765`. Leave that terminal open.
Coturn stays as it is. This service is unchanged.

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

Expect `status registered`. Read the ID and password from that
terminal.

On the inventory text, when those devices are plugged in:

- `wlp0s20f3` is the default route. `enx00e04c2c4570` is local-only.
- The follower by-id `5B61033180` is marked default. The leader
  `5B3E090040` is listed and is not the default.
- `USB2.0_CAM1` is listed. Names containing `Metadata` are absent.
- The camera page is `http://<tailscale-ipv4>:8889/cam/`, or `(none)`
  when `tailscale ip -4` does not return an address.

Type `n` and press Enter for a new password. The next login uses the
password printed after that. Type `q` or Ctrl-C to quit.

## 4. This PC — log in

```bash
printf 'robot id: '; read -r ROBOT_ID
printf 'password: '; read -r ROBOT_PASSWORD
export ROBOT_ID ROBOT_PASSWORD
python3 - << 'PY'
import asyncio, json, os
from websockets.asyncio.client import connect
URI = "ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765"
async def main():
    async with connect(URI) as ws:
        await ws.send(json.dumps({
            "v": 1, "type": "login",
            "robot_id": os.environ["ROBOT_ID"],
            "password": os.environ["ROBOT_PASSWORD"],
        }))
        first = json.loads(await ws.recv())
        print("operator", first)
        if first.get("type") != "logged_in":
            return
        print("operator", json.loads(await ws.recv()))
        print("leave this running; Ctrl-C disconnects")
        await asyncio.Future()
asyncio.run(main())
PY
```

Pass: operator prints `logged_in`, then the inventory. The robot
terminal prints `status operator_attached`.

A wrong password prints `{"v": 1, "type": "error", "code": "auth"}`
and the script returns. A second login while this script is still
running prints `busy`. Ctrl-C, then run it again: `logged_in` and
inventory once more. `unset ROBOT_PASSWORD` when finished.
