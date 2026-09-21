# ICE / STUN / TURN (direct subpaths)

This tree is **not** mlink and **not** teleop until a later plan wires
a nominated path into `mlink-transport/`.

**Start here:** [`IMPLEMENTATION.md`](IMPLEMENTATION.md) — status
board, locked decisions, milestone contracts, session prompts.

## NAT traversal (sheet 1)

Two NAT’d nodes open an outer UDP path with ICE. Each node runs the
same Python `aioice` client (`role: controlling` or `controlled`).
STUN Binding learns the mapped public IP:port on the configured NIC.
A small WebSocket service on the EC2 Elastic IP exchanges ICE
username, password, and candidate lists. Signalling does not relay
application bytes.

Both sides punch at once with authenticated ICE connectivity checks
(ICE-PWD). If a direct pair succeeds, that pair is nominated and
data is A↔B UDP (EC2 is unused for data). If punch fails, both
Allocate on **coturn** at the Elastic IP (`:3478`); coturn copies
packets A → EC2 → B. We do not write a TURN server.

Hello-world payload is plaintext `hello` / `hello-ack`. Tailscale is
SSH/mgmt only — never an ICE NIC (`tailscale0` / `100.x`).

## Hello-world flow (T1–T5)

```text
                    EC2 (Elastic IP)
           ┌────────────────────────────────┐
           │  signalling  WS  (candidates)  │  T2
           │  coturn  STUN :3478            │  T3
           │  coturn  TURN  :3478 + relays  │  T5
           └────────────┬─────────┬─────────┘
                        │         │
                 NAT    │         │    NAT
                        ▼         ▼
                 ┌──────────┐  ┌──────────┐
                 │ node A   │  │ node B   │
                 │ aioice   │  │ aioice   │
                 │ 1 NIC    │  │ 1 NIC    │
                 │ controlling│ │ controlled│
                 └──────────┘  └──────────┘

Punch OK:   A ──────── UDP ──────── B      (EC2 not on data path)
Punch fail: A ──UDP── coturn eip ──UDP── B
After nominate: echo b"hello" / b"hello-ack"
```

Two NAT’d nodes, one NIC each. No mlink, no video, no gripper.

## How to run (T1–T5)

Copy `config/example.yaml` to `config/local.yaml` (gitignored). Fill
`bind_ip`, STUN/TURN host, signalling URL, and secrets there.

```bash
cd turn
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 -m pytest -q
```

Later milestones (stubs until those land):

```bash
# T2 signalling (EC2 or localhost; from this directory)
python3 -m signalling.server --bind 0.0.0.0 --port 8765
# from repo root: python3 -m turn.signalling.server --bind 127.0.0.1 --port 8765

# T3 STUN gather (one NIC = bind_ip). Expect host + srflx public ip:port.
python3 -m agent gather --config config/local.yaml
# from repo root: python3 -m turn.agent gather --config turn/config/local.yaml

# T4 punch + echo (TURN disabled). Signalling first, then both nodes:
python3 -m signalling.server --bind 0.0.0.0 --port 8765
# each node: role controlling vs controlled, bind_ip = that NIC, TURN unused
python3 -m agent run --config config/local.yaml
# expect path=direct and hello-ack (host or srflx). Ctrl-C to stop.
# from repo root: python3 -m turn.agent run --config turn/config/local.yaml

# T5 TURN fallback (coturn on EC2; ice_policy: relay)
python3 -m agent run --config config/local.yaml           # TBD
```

aioice has no `ifname` argument. Gather binds `bind_ip` only (one NIC; never
`tailscale0` / `100.x`). When `ifname` is set, we apply `SO_BINDTODEVICE` on
the gather sockets after aioice creates them. If that fails (EPERM, missing
iface), gather still uses `bind_ip` and the CLI prints a warning.
