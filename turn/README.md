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
# T2 signalling (EC2 or localhost)
python3 -m signalling.server --bind 0.0.0.0 --port 8765   # TBD

# T3 STUN gather
python3 -m agent gather --config config/local.yaml        # TBD

# T4 punch + echo (TURN disabled)
python3 -m agent run --config config/local.yaml           # TBD

# T5 TURN fallback (coturn on EC2; ice_policy: relay)
python3 -m agent run --config config/local.yaml           # TBD
```
