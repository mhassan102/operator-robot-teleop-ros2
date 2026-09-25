# T6 test steps

NAT class, mapping idle lifetime, and the keepalive interval. Three
hosts. No mlink, no web console, no gripper, no `g`/`h` jog. These
commands do not join a room. Do not start a daemon, and do not join
`so-arm101`.

Coturn stays as it is. Do not rebuild it. Do not change UDP `3478` or
the relay range UDP `50000-50100`. Do not change the Linux default
route. Do not bind `tailscale0` or use `100.x` as a bind, STUN, TURN,
or peer address.

`stun-respond` is a test Binding responder. It is not coturn.

A previous lab saw this PC accept return packets only from
`3.227.234.95:3478`, and saw the laptop mapping change public port.
Record what this run prints. Do not treat an old public address as
the expected mapped port.

## 0. Pytest (this PC)

```bash
cd ~/robots/turn
python3 -m pytest -q
```

Expect the suite passed. It does not contact EC2 and it does not run
the idle-lifetime probe.

## 1. Copy the tree

From `~/robots` on this PC. The excludes are caches, `.venv`, and the
gitignored secret yaml files. Each machine keeps its own
`config/local.yaml`.

Laptop:

```bash
rsync -az --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' --exclude '.venv' \
  --exclude 'config/local.yaml' --exclude 'config/generic.yaml' --exclude 'config/no_turn.yaml' \
  --exclude 'config/local_op.yaml' --exclude 'config/local_edge.yaml' \
  --exclude 'scripts/coturn.env' \
  turn/ muhammad-osama@100.120.193.52:/home/muhammad-osama/teleops_hassan/turn/
```

EC2 (so `stun-respond` is on the host that already runs coturn). Do
not restart signalling. Do not touch the coturn container.

```bash
rsync -az --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' --exclude '.venv' \
  --exclude 'config/local.yaml' --exclude 'config/generic.yaml' --exclude 'config/no_turn.yaml' \
  --exclude 'config/local_op.yaml' --exclude 'config/local_edge.yaml' \
  --exclude 'scripts/coturn.env' \
  turn/ ec2-user@ec2-3-227-234-95.compute-1.amazonaws.com:/home/ec2-user/turn/
```

## 2. EC2 — second STUN port

Host: `ec2-user@ec2-3-227-234-95.compute-1.amazonaws.com`.

Leave coturn and signalling running. In the security group that
already allows UDP `3478` (`sg-3c8c470f` on the 2026-09-22 host), add
inbound UDP `3479` to `3.227.234.95`. Leave UDP `3478` and UDP
`50000-50100` as they are.

On EC2, same `python3` that already runs signalling. No new venv.

```bash
python3 -c 'import aioice'
cd ~/turn
python3 -m agent stun-respond --bind 0.0.0.0 --port 3479
```

Expect `stun-respond 0.0.0.0:3479`. Leave it in the foreground.
Ctrl-C stops only this process.

If `import aioice` fails, `python3 -m pip install --user aioice` with
that same interpreter. Do not rebuild coturn.

If UDP `3479` is not opened, skip `stun-respond`. On both agents set
`stun_server_2: stun.l.google.com` and `stun_port_2: 19302`. The
laptop often gets no reply from Google. It must be allowed to print
`class=untested`.

## 3. This PC (controlling)

```bash
ip -4 route get 1.1.1.1
```

Use the wifi `src`. Last seen: `192.168.222.56`. Put that address in
gitignored `~/robots/turn/config/local.yaml` as `bind_ip`. Leave
`role: controlling` and `room: hello-world`. Do not edit
`config/local_op.yaml`.

```yaml
stun_server: 3.227.234.95
stun_port: 3478
stun_server_2: 3.227.234.95
stun_port_2: 3479
```

`keepalive_s` may stay omitted (default 15). Do not set
`mapping_ttl_s` until a hold below reports `changed_after`.

Class, then a short listen. The class line is printed before the
wait. The socket stays open.

```bash
cd ~/robots/turn
python3 -m agent nat --config config/local.yaml --listen-s 30
```

Expect one line, then a wait. `dest1` and `dest2` are the resolved
addresses, so a hostname is printed as an IP.

```text
nat class=<endpoint-independent|endpoint-dependent|untested> detail=<...> mapped1=<ip:port> mapped2=<ip:port|missing> dest1=3.227.234.95:3478 dest2=3.227.234.95:3479
```

Record it.

- `detail=endpoint-independent` — same mapped ip:port, two different server IPs.
- `detail=port-independent` — same mapped ip:port, one server IP, two ports. The class is `endpoint-independent`. This is not a two-IP proof.
- `detail=address-and-port-dependent` — mapped port changed, one server IP, two ports. The class is `endpoint-dependent`.
- `detail=address-dependent-unconfirmed` — mapped port changed, two server IPs, no same-IP sample. The class is `endpoint-dependent`.
- `class=untested` — no second response (`mapped2=missing`).

While that process is still waiting, from EC2 send one UDP payload
`nat-probe` to `mapped1` from an ephemeral source port. Egress only.
Do not add an inbound rule for it.

```bash
python3 -c 'import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.sendto(b"nat-probe", ("MAPPED_IP", MAPPED_PORT)); print("sent-from", s.getsockname())'
```

The agent then prints `nat filtering=received` or
`nat filtering=none`. Send as soon as `mapped1` is on the screen.
Record the line. `filtering=none` from an ephemeral EC2 port matches
the earlier observation that this PC accepted return packets only
from `3.227.234.95:3478`.

Then the idle probe. One fresh socket per hold, toward
`stun_server` (`3.227.234.95:3478`) only. Each hold prints `start`,
goes quiet, then prints the mapped ports. It stops at the first
changed port.

```bash
python3 -m agent nat-ttl --config config/local.yaml --holds 20,40,60
```

Expect either:

```text
nat_ttl changed_after=<seconds>
```

or, if every hold keeps the same mapped port:

```text
nat_ttl inconclusive_within=60
```

`inconclusive` means the port did not change. A port-preserving NAT
can hand the same port out again, so that line does not prove the
mapping stayed alive. The same sentence is printed on stderr. Record
the `hold=` lines too. Those ports are this run's ports. The refresh
numbers below assume `keepalive_s` is omitted (the default is 15).

Refresh soak. This is STUN Binding toward `stun_server` on one
socket. It is not an aioice consent loop.

If the probe printed `changed_after=H`, refresh at
`min(15, max(1, H//2))` seconds and pick a duration longer than `2H`:

| changed_after | refresh | duration |
| ------------- | ------- | -------- |
| 20 | 10 | 50 |
| 40 | 15 | 90 |
| 60 | 15 | 130 |

For `changed_after=40`:

```bash
python3 -m agent nat-ttl --config config/local.yaml --refresh 15 --duration 90
```

Use the table row for the `H` this run printed. For 20 that is
`--refresh 10 --duration 50`. For 60 that is `--refresh 15 --duration 130`.

If the result was `inconclusive_within=60`, there is no killing hold.
Do not set `mapping_ttl_s`. Soak longer than twice that hold:

```bash
python3 -m agent nat-ttl --config config/local.yaml --refresh 15 --duration 130
```

Pass:

```text
nat_ttl refresh unchanged interval_s=<n> duration_s=<n> mapped=<ip:port>
```

The mapped port is the port from this soak, not an older address.
`nat_ttl refresh changed` means the interval did not hold the
mapping. Record it. Do not raise the interval.

After a real `changed_after=H`, you may set `mapping_ttl_s: H` in the
gitignored yaml. Leave `keepalive_s` omitted unless you want a lower
cap. The interval on a later run is `min(keepalive_s or 15, max(1, H//2))`.

## 4. Laptop (controlled)

User `muhammad-osama`. Tree `~/teleops_hassan/turn`. Miniforge base.
No new venv. Use that shell's `python3`.

```bash
ip -4 route get 1.1.1.1
```

Last seen wifi `src`: `10.255.254.58`. Edit gitignored
`~/teleops_hassan/turn/config/local.yaml`: `role: controlled`, that
`bind_ip`, and the same `stun_server` / `stun_server_2` pair as this
PC. Room stays `hello-world`. Do not edit `local_edge.yaml`.

```bash
cd ~/teleops_hassan/turn
python3 -m agent nat --config config/local.yaml --listen-s 30
python3 -m agent nat-ttl --config config/local.yaml --holds 20,40,60
python3 -m agent nat-ttl --config config/local.yaml --refresh <seconds> --duration <seconds>
```

During `--listen-s`, send the same EC2 one-liner at this host's
`mapped1`. Choose `--refresh` and `--duration` the same way as on
this PC, from this host's own `changed_after` or `inconclusive` line.

If `stun_server_2` is Google and this wifi drops the reply, expect
`class=untested` and `mapped2=missing`. Record that. A previous lab
saw this NIC change public port; record the class this run actually
prints.

## 5. What a pass is

On each NIC, all three of these, copied from the terminal:

1. The `nat class=` line.
2. `nat_ttl changed_after=<seconds>` or `nat_ttl inconclusive_within=<seconds>`.
3. `nat_ttl refresh unchanged` whose duration is longer than twice the hold that killed an idle mapping. If no hold killed it, the duration is longer than twice the longest hold.

Also keep `nat filtering=` and the EC2 `sent-from` line.

## 6. Keepalive line (not this lab)

`python3 -m agent run` prints the policy after the existing `path=`
line. aioice already refreshes the nominated pair about every 5
seconds. This milestone does not send a second refresh on that
socket. TURN allocation refresh stays aioice's existing lifetime
refresh. Room for that optional run is `hello-world`. Do not run it
for this check, and do not join `so-arm101`.

With no `mapping_ttl_s`:

```text
keepalive path=<direct|turn> interval_s=15 basis=yaml-default ttl_s=unknown consent_s=5
```

With `mapping_ttl_s` set, `basis=measured-ttl`. If that TTL is 5
seconds or less, the process warns that the consent interval is not
below the TTL.
