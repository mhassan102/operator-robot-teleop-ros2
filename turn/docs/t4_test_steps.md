# T4 test steps

Two agents: STUN gather → signalling (ICE username/password + candidates) →
authenticated `connect()` → nominate **direct** UDP → echo `hello` /
`hello-ack`. TURN is **off** for this milestone (`turn_server: null` in yaml).
No mlink.

`path=direct` means both ends of the nominated pair are `host` or `srflx`.
EC2 is **not** on the data path (signalling only).

Addresses: operator wifi (controlling; recently `192.168.222.43`), laptop
wifi `10.255.254.58` (controlled). Signalling:
`ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765`, room `hello-world`.
Do not bind `tailscale0` / `100.x`.

Laptop: `~/teleops_hassan/turn`. Operator: `~/robots/turn`. EC2: `~/turn`.

## 1. Pytest (operator, loopback — enough to pass T4 software)

```bash
cd ~/robots/turn
. .venv/bin/activate
python3 -m pytest -q
```

Expect all passed (two agents on `127.0.0.1`, `path=direct`, `hello-ack`).
No EC2, no laptop.

## 2. Signalling on EC2

SG: inbound TCP `8765`. Leave this running:

```bash
cd ~/turn && . .venv/bin/activate
python3 -m signalling.server --bind 0.0.0.0 --port 8765
```

Expect `signalling ws://0.0.0.0:8765`. It does not print `hello`.

## 3. Yaml (TURN off)

Both `config/local.yaml`:

- operator: `role: controlling`, `bind_ip` = wifi, `stun_server:
  stun.l.google.com`, `stun_port: 19302`, `turn_server: null`,
  `ice_policy: all`, `signalling_url:
  ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765`, `room: hello-world`
- laptop: `role: controlled`, `bind_ip: 10.255.254.58`, same STUN / URL /
  room, `turn_server: null`

If `turn_*` is set, this is no longer a T4-only run (T5 may nominate relay).

## 4. Agents

Start both within about 60 seconds.

Operator:

```bash
cd ~/robots/turn
. .venv/bin/activate
python3 -m agent run --config config/local.yaml
```

Laptop:

```bash
cd ~/teleops_hassan/turn
. .venv/bin/activate
python3 -m agent run --config config/local.yaml
```

**Pass:** both print `path=direct` and `hello` / `hello-ack`, then
`holding path … Ctrl-C`.

**Known fail on this laptop wifi:** Google STUN UDP is filtered, so the
laptop has no `srflx`. ICE never learns a public mapping; punch cannot
nominate. Expect `ICE negotiation failed` / `path=failed`. Not a T4 code
bug. T5 TURN on `3.227.234.95` is the path that worked (see
`t5_test_steps.md`).

## 5. Stop

Ctrl-C both agents and the signalling process.
