# T11 test steps

One wifi NIC on each lab host. mlink control rides the nominated ICE
socket. The camera stays on Tailscale. ROS containers and EC2 coturn /
signalling are unchanged.

Do not also run `python3 -m agent run`. The mlink daemon starts the ICE
agent. A second agent in room `so-arm101` is rejected.

`start_operator_mlink.sh` and `start_robot_mlink.sh` only start the ROS
containers. They do not start ICE.

Addresses for the 2026-09-23 run: operator wifi `192.168.222.56`
(controlling), laptop wifi `10.255.254.58` (controlled), coturn
`3.227.234.95` UDP `3478`, relay ports `50000-50100`. TURN user
`labturn`. Password stays in the gitignored yaml. Room `so-arm101`.
`ice_policy: all`.

Confirm `bind_ip` before every run. DHCP moves it.

```bash
ip -4 route get 1.1.1.1
```

Use the `src` address on wifi. Do not use `tailscale0` or `100.x`.

Operator file: `turn/config/local_op.yaml`.
Laptop file: `turn/config/local_edge.yaml`.

## 1. Coturn and signalling (EC2)

```bash
ssh ec2-user@ec2-3-227-234-95.compute-1.amazonaws.com
sudo docker start coturn
cd ~/turn && python3 -m signalling.server --bind 0.0.0.0 --port 8765
```

Expect `signalling ws://0.0.0.0:8765`, then silence. Leave it running.
If the `coturn` container is missing, `bash ~/coturn/run-coturn.sh`.

## 2. Copy to the laptop

From the operator PC, in the repo root:

```bash
rsync -az --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' --exclude '.venv' \
  mlink-transport/ muhammad-osama@100.120.193.52:/home/muhammad-osama/teleops_hassan/mlink-transport/

rsync -az --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' --exclude '.venv' \
  --exclude 'config/local.yaml' --exclude 'config/generic.yaml' --exclude 'config/no_turn.yaml' \
  --exclude 'scripts/coturn.env' \
  turn/ muhammad-osama@100.120.193.52:/home/muhammad-osama/teleops_hassan/turn/
```

That copies `local_op.yaml` and `local_edge.yaml`. On the laptop, fix
`bind_ip` in `turn/config/local_edge.yaml` if `ip -4 route get 1.1.1.1`
disagrees. Use the Miniforge `(base)` shell, where `python3` imports
`aioice`.

## 3. Daemons

Laptop camera first (Tailscale video, not mlink):

```bash
cd /home/muhammad-osama/teleops_hassan/video && ./start.sh
```

Laptop mlink (controlled, creates the room). Leave it running:

```bash
cd /home/muhammad-osama/teleops_hassan/mlink-transport
./scripts/start_daemon.sh edge --ice
```

Within about 60 seconds, on the operator PC:

```bash
cd ~/robots/mlink-transport
./scripts/start_daemon.sh op --ice
```

Pass: both print `path=turn` or `path=direct`, then `ice=up`, loss `0`.
No `tailscale0` and no `100.` in the path line. App ports stay
`127.0.0.1:5501/5502` (operator) and `127.0.0.1:5503/5504` (laptop).
`listen_media` stays `-`.

The 2026-09-23 run nominated coturn, not a direct punch. Both sides
gathered host, srflx, and relay. Signalling swapped those candidates in
room `so-arm101`. The selected sends were:

- Operator: relay `3.227.234.95:50014` → laptop relay `3.227.234.95:50071`
- Laptop: relay `3.227.234.95:50071` → operator srflx `43.246.227.66:47832`

Relay ports change every run. srflx was gathered on both sides
(operator `43.246.227.66`, laptop `86.98.43.27`) and was not a direct
pair. mlink heartbeat RTT was about 390–400 ms.

## 4. Containers and gripper

Laptop, after `path=` is up:

```bash
cd /home/muhammad-osama/teleops_hassan/teleoperation-prototype
./scripts/start_robot_mlink.sh --real-arm
```

Wait until healthy and `TELEOP_ARM=real`. Then on the operator PC:

```bash
cd ~/robots/teleoperation-prototype
./scripts/start_operator_mlink.sh --remote-laptop
```

Open:

```text
http://127.0.0.1:8090/?cam=http://100.120.193.52:8889/cam
```

The `cam` host is still the laptop over Tailscale. When the HUD says
CONNECTED, one tap `g` (tiny open), then one tap `h`. `win` on the
`ice=up` line climbs once those app datagrams flow. It counts delivered
teleop payloads, not mlink's own heartbeats.

## 5. Stop

Close the browser tab first so the gripper torque turns off. Then on
the operator PC, in `teleoperation-prototype`, `./scripts/stop_mlink.sh`,
and Ctrl-C the mlink-op terminal. On the laptop, `./scripts/stop_mlink.sh`,
Ctrl-C mlink-edge, then `video/stop.sh` if the camera should stop.
Coturn and signalling can stay up.
