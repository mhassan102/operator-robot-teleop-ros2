# T3 test steps

STUN gather on **one NIC** per machine. No signalling, no ICE `connect()`,
no `hello`, no mlink. Each host only asks STUN: what public IP:port did
NAT give this socket?

Addresses for this lab: operator wifi (discover with `ip -4 route get 1.1.1.1`;
recently `192.168.222.43` or `192.168.1.103`), laptop wifi `10.255.254.58`
(`wlp0s20f3`). Do **not** bind `tailscale0` or `100.x`.

Google STUN: `stun.l.google.com` UDP `19302`. Coturn STUN (if T5 coturn is
up): `3.227.234.95` UDP `3478`.

Laptop path: `~/teleops_hassan/turn`. Operator: `~/robots/turn`.

## 1. Pytest (operator, no WAN)

```bash
cd ~/robots/turn
. .venv/bin/activate
python3 -m pytest -q tests/test_ice.py tests/test_config.py
```

Expect tests passed. Live STUN may be skipped (`s`).

## 2. Operator gather (Google)

`config/google.yaml` or `config/local.yaml`: `bind_ip` = this PC’s wifi,
`stun_server: stun.l.google.com`, `stun_port: 19302`, `turn_server: null`.

```bash
cd ~/robots/turn
. .venv/bin/activate
python3 -m agent gather --config config/google.yaml
```

Expect a `host` line (LAN) and an `srflx` line (public IP:port), for example:

```text
bind_ip=192.168.222.43 ifname=- stun=stun.l.google.com:19302
host 192.168.222.43:<port>
srflx <public>:<port> related 192.168.222.43:<port>
```

Fail: `no srflx` — STUN timeout or wrong `bind_ip`.

## 3. Laptop gather (Google)

Same yaml, `bind_ip: 10.255.254.58`, `role: controlled`.

```bash
cd ~/teleops_hassan/turn
. .venv/bin/activate
python3 -m agent gather --config config/local.yaml
```

**Known:** this laptop wifi often gets **no** Google STUN reply (UDP STUN/VoIP
filtered; DNS UDP still works). Then you only see `host 10.255.254.58:<port>`
and `no srflx`. That is not a T3 code bug.

## 4. Optional — gather against coturn STUN

If coturn is running on EC2 (see `t5_test_steps.md`):

```bash
python3 scripts/stun_probe.py 3.227.234.95 3478
python3 -m agent gather --config config/local.yaml
```

Set `stun_server: 3.227.234.95`, `stun_port: 3478`. Expect `srflx` even on
the laptop if UDP to EC2 `:3478` is allowed.

## 5. Stop

Gather exits by itself. No long-running process.
