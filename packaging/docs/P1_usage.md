# P1 lab check

Pair a robot socket and an operator socket through the signalling
process already listening on EC2 TCP 8765. Coturn stays up. No arm,
camera, or mlink.

The robot script sends inventory once, after the first
`operator_attached`. A later login still prints `logged_in` and then
waits. Start the robot script again to see inventory a second time.

## 1. This PC — copy onto EC2

```bash
cd /home/muhammadhassan/robots
rsync -av turn/signalling/server.py ec2-user@3.227.234.95:~/turn/signalling/server.py
rsync -av --exclude tests --exclude __pycache__ packaging/ ec2-user@3.227.234.95:~/turn/packaging/
```

## 2. EC2 — restart signalling

Stop the process `ss -ltnp | grep 8765` shows, then:

```bash
cd ~/turn && . .venv/bin/activate
python3 -m signalling.server --bind 0.0.0.0 --port 8765
```

Expect `signalling ws://0.0.0.0:8765`. Leave that terminal open.

## 3. Robot PC — register, and leave it open

```bash
cd ~/teleops_hassan/turn && . .venv/bin/activate
python3 - << 'PY'
import asyncio, json
from websockets.asyncio.client import connect
URI = "ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765"
async def main():
    async with connect(URI) as ws:
        await ws.send(json.dumps({
            "v": 1, "type": "register", "robot_id": "123456789",
            "password": "AB23CD45", "hostname": "AUTOOS-DEV-MUHAMMADOSAMA",
        }))
        print("robot", await ws.recv())
        print("robot", await ws.recv())
        await ws.send(json.dumps({
            "v": 1, "type": "inventory",
            "interfaces": [{"name": "wlp0s20f3", "ipv4": "10.255.254.58", "up": True, "default_route": True}],
            "arms": [], "videos": [], "camera_page": "",
        }))
        await asyncio.Future()
asyncio.run(main())
PY
```

Expect `{"v": 1, "type": "registered"}` at once. `operator_attached`
prints after step 4.

## 4. This PC — log in

```bash
python3 - << 'PY'
import asyncio, json
from websockets.asyncio.client import connect
URI = "ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765"
async def main():
    async with connect(URI) as ws:
        await ws.send(json.dumps({
            "v": 1, "type": "login", "robot_id": "123456789", "password": "AB23CD45",
        }))
        print("operator", await ws.recv())
        print("operator", await ws.recv())
asyncio.run(main())
PY
```

Pass: operator prints `logged_in`, then the inventory (`wlp0s20f3`).
Robot prints `operator_attached`.

A wrong password prints `{"v": 1, "type": "error", "code": "auth"}`.
The script then waits on a second message; Ctrl-C is the end of that
check. A second login while the first operator socket is still open
prints `busy`.
