# ICE / STUN / TURN — implementation plan

Read this file first in a new session. `T1`–`T10` stay inside
`turn/` and do not edit mlink, teleop, or video. `T11` is the
product step that wires the nominated socket into mlink for one
ISP (control, then video). A `T1`–`T10` session must not start
`T11`. Do not renumber `T6`–`T10`; `T11` stays after them even
though it is implemented first.

This session (the planner) writes this file only. **Do not write ICE /
signalling / coturn code until a later session is given a resume
prompt below.**

Sheet task: *Direct Subpaths ICE/STUN, TURN* (21 subtasks). Product
call: production-grade ICE — **direct UDP when hole punch works**,
**TURN (coturn on EC2) when it does not**. We are **not** writing a
TURN server. We run **coturn** and put an ICE **client** (Python
`aioice`) on both nodes.

---

## How to use this file

**Planner / check-in session:** after an implementation session
finishes a milestone (and the user has verified the test steps):

1. Confirm the contract below
2. Set that row `STATUS: done` (keep `commit: remaining` until the
   user asks to commit)
3. Commit only if the user in that session asked (**not push**)
4. Set `commit: done` after the commit
5. Paste the next-session prompt

**Implementation session:**

- Read this file from the start, then only files under `turn/`.
- Implement **exactly one** milestone (`T1` … `T11`). Stop even if
  the next one looks small.
- Do not re-open locked decisions.
- Do not start a `blocked` or `on hold` row.
- Do not `git commit` unless the user in that session explicitly asks.
- After the milestone works: set that row `STATUS: done`, keep
  `commit: remaining`, list files changed, **print the test steps for
  that milestone**, stop.
- `T1`–`T10`: do not touch mlink, ROS, the console, or the gripper.
  `T11` is the only milestone that edits mlink and the video path.

**Hello-world first.** `T1`–`T5` are the 5-day slice (sheet subtasks
1–3, 5, 7–8, 11, 19). They are done. `T11` control is done. Do not start `T6` until the
user asks. Leave the `T6`–`T10` rows where they are.

---

## Status board

Update `STATUS` when a milestone finishes. Values: `remaining`,
`done`, `blocked`, `on hold`.

`commit`: `remaining` until the user asks to commit that milestone;
then `done`.

| ID | Milestone | Sheet subtasks | STATUS | commit |
| -- | --------- | -------------- | ------ | ------ |
| T0 | This plan | — | done | done |
| T1 | Skeleton + locked ICE design | 1 | done | done |
| T2 | Signalling (candidate exchange) | 5 | done | done |
| T3 | STUN gather (mapped IP:port) | 2, 3 | done | done |
| T4 | Punch + auth checks + nominate + echo | 7, 8, 11 | done | done |
| T5 | TURN fallback when punch fails | 19 | done | done |
| T6 | NAT type + mapping TTL + keepalives | 4, 13, 14 | remaining | remaining |
| T7 | Channel/ISP tags + all NIC pairs | 6, 10 | remaining | remaining |
| T8 | Source filter, quality metrics, expire, unauth | 9, 12, 20, 21 | remaining | remaining |
| T9 | NAT rebind + re-check | 15, 16 | remaining | remaining |
| T10 | Path-fail vs loss + recovery | 17, 18 | remaining | remaining |
| T11 | mlink + ICE, one wifi path (control on TURN) |  | done | done |

**Next to implement:** `T6` when the user asks.
Hello-world `T1`–`T5` is done. `T11` control is done (video stays on
Tailscale). Do **not** start `T6` until the user asks. Do not renumber
`T6`–`T10`.

**Hello-world (T1–T5):** two NAT’d nodes, one NIC each, STUN,
signalling, punch **or** TURN, echo `hello`. No mlink, no video, no
gripper, no all-NIC matrix, no rebind/recovery.

---

## Sheet subtask map

| # | Sheet line | Milestone | Hello-world? |
| - | ---------- | --------- | ------------ |
| 1 | Define NAT traversal for outer UDP paths | T1 | yes |
| 2 | STUN independently on each NIC | T3 (one NIC in HW) | yes (1 NIC) |
| 3 | Discover public IP and port mapping | T3 | yes |
| 4 | Endpoint-dependent vs independent NAT | T6 | no |
| 5 | Exchange candidates via signalling | T2 | yes |
| 6 | Associate candidate with channel and ISP | T7 | no |
| 7 | Simultaneous UDP hole punching | T4 | yes |
| 8 | Authenticated connectivity checks | T4 | yes |
| 9 | Validate source before accepting a subpath | T8 | no |
| 10 | All permitted channel-pair combinations | T7 | no |
| 11 | Nominate validated direct UDP subpaths | T4 | yes |
| 12 | Setup time and initial quality | T8 | no |
| 13 | Estimate NAT mapping lifetime | T6 | no |
| 14 | Path-specific keepalive intervals | T6 | partial in T4 |
| 15 | Detect public address or port rebinding | T9 | no |
| 16 | Re-run checks after rebinding | T9 | no |
| 17 | Direct-path failure vs packet loss | T10 | no |
| 18 | Controlled direct-path recovery | T10 | no |
| 19 | TURN when direct punch fails | T5 | yes |
| 20 | Expire stale candidates and mappings | T8 | no |
| 21 | Unauthenticated checks must not flood state | T8 | no |

---

## Locked decisions (do not re-open)

1. **Language:** Python 3, pytest. ICE client = **`aioice`** (do not
   write STUN/ICE/TURN from scratch). TURN **server** = **coturn**
   (Docker on EC2). Do not implement coturn.
2. **Three processes, not one product:** (a) ICE agent on operator
   node, (b) ICE agent on robot node — **same code**, different
   config (`ice_controlling` true/false, local NIC), (c) EC2:
   coturn + the signalling service we write.
3. **Direct first, TURN if punch fails.** Nominated path is either
   A↔B UDP (EC2 unused for data) or A→EC2→B (coturn copies after
   Allocate). Hello payload is plaintext `hello` / `hello-ack`.
   Payload encryption is **not** this plan (product F20 / later).
   ICE-PWD on connectivity checks (subtask 8) is **not** F20.
4. **No mlink in T1–T10.** Those agents send `hello` on the ICE
   socket after `connect()`. Do not change `mlink-transport/` from
   a `T1`–`T10` session. `T11` is the integration: mlink sends and
   receives application bytes on the socket the ICE agent already
   nominated. Do not start `T11` inside a `T1`–`T10` session.
5. **Tailscale is SSH/mgmt only.** Do not gather ICE candidates on
   `tailscale0`. Do not use `100.x` as STUN/TURN/peer.
6. **Hello-world is one NIC per node.** `T7` adds eth/wifi/cell
   pairs. T3 may *bind* a configured `ifname` but does not require
   three uplinks to pass.
7. **Signalling is a small WebSocket (or HTTP POST) service** on the
   EC2 public IP (TLS later if needed). First version may be
   plaintext WS on a lab port, token optional. It exchanges ICE
   username/password + candidate list. It does **not** relay `hello`.
8. **Do not change `ip route`.** Bind ICE sockets to the intended
   NIC (`bind_ip` and `SO_BINDTODEVICE` when `ifname` is set), same
   idea as mlink Stage 3.
9. **EC2 needs a static Elastic IP.** coturn `--external-ip=<eip>`.
   Security group: UDP 3478, UDP relay range (coturn min/max port),
   TCP/WS signalling port. SSH for mgmt.
10. **Managed TURN (Twilio, Cloudflare) is out of T1–T10.** We run
    coturn. A later swap of `turn_server:` in YAML is allowed; do
    not take a Twilio dependency now.
11. **Tests:** pytest for protocol/unit (no live NAT required). Lab
    steps in each milestone are **manual** on two hosts + EC2.
    Network-using tests must skip cleanly without STUN/EC2.
12. **T11 is one wifi NIC.** mlink’s app face stays `127.0.0.1`.
    The WAN hop is the nominated ICE socket (direct, or coturn when
    the punch fails). The signalling room is the two ICE agents
    only. ROS, the camera, MediaMTX, and the browser do not join.
    Chrome’s WebRTC stays on the operator localhost. A second ISP
    is not `T11`. Tailscale is not a data path.

---

## Target architecture

### Hello-world (T1–T5)

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

### After T6–T10 (same boxes, more ICE state)

STUN on every NIC, all A-nic×B-nic pairs, keepalive vs NAT TTL,
rebind → re-ICE, fail vs loss → recover or TURN, expire, ICE-PWD
required before allocating state. `T11` does not wait for this.
`T6`–`T10` stay later hello-world work.

### T11 — one ISP through mlink

```text
browser  http://127.0.0.1:8090/
  → operator backend
  → UDP 127.0.0.1:5501/5502  (control)
  → UDP 127.0.0.1:5004       (RTP, after control passes)
  → mlink-op
       one wifi NIC, socket nominated by the ICE agent
       path=direct:  operator public ↔ laptop public
       path=turn:    operator host → coturn relay → laptop
  → mlink-edge
  → UDP 127.0.0.1:5503/5504  → robot bridge → /teleop/*
  → UDP 127.0.0.1:5004       → (no player on the laptop)

operator PC, after the RTP arrives:
  MediaMTX 127.0.0.1:8889/cam
  → Chrome WHEP from the console (localhost only)
```

The heartbeat and RTP enter mlink on localhost. mlink writes them
on the nominated socket. They do not go backend → ICE → mlink.
The ICE agent is the WAN end of that one path because a TURN
allocation is not a raw `peer:` address. Signalling on EC2 still
only exchanges ICE username, password, and candidates.

---

## Layout (create from T1 onward; do not invent extra trees)

```text
turn/
  IMPLEMENTATION.md     # this file
  README.md             # how to run (T1 fills commands)
  requirements.txt      # aioice, pytest, websockets (T1)
  pyproject.toml        # pytest (T1)
  config/
    example.yaml        # bind_ip, ifname, stun, turn, signalling URL
  agent/                # shared ICE agent (both nodes)
  signalling/           # WS server (runs on EC2)
  tests/
  scripts/              # run-agent, run-signalling, coturn notes
```

Secrets (TURN user/pass, signalling token) stay in a **gitignored**
local yaml or env. Example yaml has placeholders only.

---

## Milestone contracts

### T0 — This plan — STATUS: done

Plan files under `turn/`. `commit: done`.

---

### T1 — Skeleton + locked ICE design — sheet 1 — STATUS: done

**Goal.** Repo layout + a short design note in `turn/README.md` that
states: ICE/STUN, hole punch, nominate direct UDP, TURN only if punch
fails. Empty packages, dependencies, example yaml, pytest collects.

**Read:** this file (locked decisions, layout).

**Implement:**

- `turn/README.md` — hello-world flow (copy the T1–T5 diagram), how
  T1–T5 will be run (commands can be TBD stubs).
- `turn/requirements.txt` — `aioice`, `pytest`, `websockets`, `PyYAML`.
- `turn/pyproject.toml` — pytest `testpaths = ["tests"]`.
- `turn/config/example.yaml` — keys: `role` (`controlling`|
  `controlled`), `bind_ip`, `ifname` (optional), `stun_server`,
  `stun_port`, `turn_server`, `turn_port`, `turn_user`,
  `turn_password`, `signalling_url`, `room`.
- `turn/agent/__init__.py`, `turn/signalling/__init__.py`.
- `turn/tests/test_config.py` — load example yaml (required keys).
- `turn/.gitignore` — `__pycache__/`, `.venv/`, `config/local.yaml`.

**Do not:** run coturn, gather candidates, implement signalling,
import mlink, call Twilio.

**Verify:**

```bash
cd turn && python3 -m pytest -q
```

README states the NAT-traversal mechanism (sheet 1) in ≤20 lines.

**When done:** `STATUS: done`, `commit: remaining`, list files, stop.

T1 landed: skeleton + README sheet-1 design. `commit: done`.

---

### T2 — Signalling — sheet 5 — STATUS: done

**Goal.** Two agents can join a `room` and exchange ICE **username,
password, and candidate list**. Signalling does not carry `hello`.

**Read:** this file, `turn/config/example.yaml`, `aioice` Connection
local_username / local_password / get_local_candidates.

**Implement:**

- `turn/signalling/server.py` — WebSocket server. Protocol (JSON):
  `join` `{room, role}`; `candidates` `{username, password,
  candidates: [{ip, port, type, foundation, component, priority,
  related?}]}`; broadcast the peer’s blob to the other member of the
  room (exactly two members).
- `turn/agent/signalling_client.py` — connect, join, send, wait for
  peer.
- pytest with an in-process server (localhost): two clients, room of
  two, each receives the other’s payload. No STUN required.

**Do not:** ICE connect, STUN, TURN, mlink.

**Verify:**

```bash
cd turn && python3 -m pytest -q
# optional manual:
# python3 -m turn.signalling.server --bind 127.0.0.1 --port 8765
```

**When done:** `STATUS: done`, `commit: remaining`, list files, stop.

T2 landed: WS signalling, two-client ICE blob exchange. `commit: done`.

---

### T3 — STUN gather — sheet 2, 3 — STATUS: done

**Goal.** On a configured NIC, gather ICE candidates and print the
**srflx** mapped public IP:port from STUN. One NIC is enough.
`ifname` set → `SO_BINDTODEVICE` on the socket used for gather if
`aioice` allows a local address; otherwise bind `bind_ip` and
document the limitation.

**Read:** aioice `Connection.gather_candidates`, this file T3.

**Implement:**

- `turn/agent/ice.py` (or similar) — build `aioice.Connection`,
  `stun_server` from yaml, `gather_candidates()`, return structured
  candidates (ip, port, type host/srflx/relay).
- CLI: `python3 -m turn.agent gather --config config/local.yaml`
  prints candidates.
- pytest: mock or construct candidate objects; **skip** live STUN if
  no network. Optional `@pytest.mark.network` against
  `stun.l.google.com:19302` or coturn.

**Do not:** `connect()`, signalling, TURN Allocate, require three
NICs, mlink.

**Verify:**

```bash
cd turn && python3 -m pytest -q
# lab (operator PC or laptop, yaml bind_ip = that NIC):
python3 -m turn.agent gather --config config/local.yaml
# expect at least one srflx (or host + srflx) line with public ip:port
```

**When done:** `STATUS: done`, `commit: remaining`, list files, stop.

T3 landed: aioice STUN gather on one NIC (`bind_ip`); print host/srflx;
`SO_BINDTODEVICE` after bind when `ifname` is set, else bind_ip only.
`commit: done`.

---

### T4 — Punch, checks, nominate, echo — sheet 7, 8, 11 — STATUS: done

**Goal.** Two agents: gather (T3), exchange via signalling (T2),
`aioice` connectivity checks (authenticated ICE-PWD — sheet 8),
nominate a **direct** pair when punch works (sheet 7, 11), send
`hello` / `hello-ack` on the ICE socket.

**Read:** aioice `add_remote_candidate`, `connect()`, send/recv;
T2+T3 code.

**Implement:**

- Agent `run` mode: gather → signalling exchange → `connect()` →
  echo. Log whether the selected pair is **host/srflx (direct)**.
- Light keepalive: aioice consent checks; do not invent T6 TTL yet.
- pytest: two agents on 127.0.0.1 with a local signalling server;
  echo succeeds (**host** candidates). No EC2 required for pytest.

**Do not:** TURN, force-relay, mlink, multi-NIC matrix, rebind.

**Verify:**

```bash
cd turn && python3 -m pytest -q
```

**Lab (hello-world direct path):** two NAT’d hosts, signalling on
EC2 (or a reachable WS), STUN = coturn or public STUN, **TURN
disabled**. Both run:

```bash
python3 -m turn.agent run --config config/local.yaml
```

Expect log `path=direct` and `hello-ack`. If punch fails in that
lab, record it; do not implement T5 in this session.

**When done:** `STATUS: done`, `commit: remaining`, list files, stop.

T4 landed: gather → signalling (ICE-UFRAG/ICE-PWD + candidates) → `connect()`
(authenticated checks) → nominate host/srflx as `path=direct` → echo
`hello` / `hello-ack`. aioice consent checks are the light keepalive.
TURN still unused. `commit: done`.

**Lab:** pytest two-agent loopback = pass. Operator STUN to
`stun.l.google.com` = srflx. Two-NAT **direct** on the laptop wifi
(`10.255.254.58`) = no Google STUN reply (UDP STUN/VoIP filtered
upstream; DNS UDP still works). Not a T4 code bug. Proceed T5.
Direct T4 WAN can be re-tried on a network that allows STUN.

---

### T5 — TURN fallback — sheet 19 — STATUS: done

**Goal.** When direct punch fails, both agents use **coturn** on the
Elastic IP. Data path is A → eip:3478 → B. Echo still `hello`.
coturn is **deployed**, not written.

**Read:** coturn docker docs, aioice `turn_server` / `turn_username`
/ `turn_password`, T4 agent.

**Implement:**

- `turn/scripts/coturn.md` (or compose yaml under `turn/scripts/`) —
  listen 3478, `--external-ip=<EIP>`, `--lt-cred-mech`, realm, user,
  relay port range, AWS SG list.
- Agent: if yaml has turn_*, pass them into `Connection`. Support
  `ice_policy: all | relay` (or equivalent filter). `relay` proves
  TURN even when punch would work.
- Log `path=turn` when the nominated pair is type **relay**.
- pytest: skip without TURN; optional mark. Do not require EC2 in CI.

**Do not:** Twilio, mlink, write a custom TURN daemon, F20 crypto.

**Verify:**

```bash
cd turn && python3 -m pytest -q
```

**Lab 0 — laptop UDP to EC2:** before ICE, prove the laptop can send
UDP to `3.227.234.95:3478` (coturn STUN Binding or `nc -u`). If that
is also filtered, UDP TURN cannot work on that wifi; document and
stop (TURN-TCP/443 is out of T5).

**Lab A — force TURN:** `ice_policy: relay`, coturn up, two nodes.
Expect `path=turn` and `hello-ack`.

**Lab B — fallback:** `ice_policy: all`, block direct (peer firewall
or policy) so checks fail on host/srflx; expect TURN then echo.

Hello-world T5 is **Lab A**. Google STUN UDP is still filtered on the
laptop wifi. Coturn STUN on `3.227.234.95:3478` is not. Relay ports
are UDP `50000-50100` (that range already reaches the instance;
`49152-49200` does not).

**Lab 2026-09-22:** Coturn `4.6.2` on EC2 (Docker, host network, UDP
only, `--external-ip=3.227.234.95/172.31.73.32`, relay `50000-50100`,
user `labturn`). Laptop STUN Binding to `3.227.234.95:3478` works
(Google STUN still filtered). Echo succeeded with `ice_policy: all`
and `path=turn` (operator host `192.168.222.43:51865` ↔ laptop relay
`3.227.234.95:50009`; coturn copies). Force-relay (`ice_policy:
relay` on both yaml files) is Lab A; optional follow-up. Steps:
`docs/t5_test_steps.md`. `commit: done`.

**When done:** `STATUS: done`, `commit: remaining`, list files, stop.
Do not start T6 until the user asks.

---

### T6 — NAT type, mapping TTL, keepalives — sheet 4, 13, 14 — STATUS: remaining

**Goal.** Classify NAT (endpoint-independent vs dependent). Measure
mapping lifetime. Set keepalive **below** that TTL per path.

**Implement:** extra STUN tests from the same local socket to two
STUN endpoints / changed peer tuples; a probe that waits until the
mapping dies (lab, long-running, not default pytest); keepalive
interval in yaml derived from measured TTL (default 15s until
measured).

**Verify:** document NAT class per lab NIC; keepalive holds a mapping
for > 2× previous observed lifetime in a soak (manual).

**Do not:** T7 multi-NIC matrix, T9 rebind, mlink.

---

### T7 — Channel tags + all NIC pairs — sheet 6, 10 — STATUS: remaining

**Goal.** Each candidate carries `ifname` + ISP/channel tag. ICE
tries every **permitted** A-channel × B-channel pair (yaml allow
list). Hello-world one-NIC still works.

**Implement:** yaml `channels: [{ifname, bind_ip, tag}]`; one
`aioice.Connection` per local channel **or** documented equivalent;
pair matrix; log which pair nominated.

**Verify:** pytest on fake channels; lab with ≥2 NICs on at least one
side if available. Skip cell if no dongle (do not block T7).

**Do not:** mlink bonding, T9/T10.

---

### T8 — Hygiene — sheet 9, 12, 20, 21 — STATUS: remaining

**Goal.** Drop packets whose source is not a nominated/check pair
(9). Log setup time and initial RTT (12). Expire stale candidates
and mappings (20). Ignore connectivity checks without valid ICE
credentials — no state flood (21).

**Implement:** source allow-list after nominate; timers; metrics on
connect; reject unauth STUN Binding before creating session state
(aioice should already auth checks — **assert** and add a test that
garbage UDP does not grow tables).

**Verify:** pytest for expire + unauth; lab log line with setup_ms
and rtt_ms.

---

### T9 — Rebind — sheet 15, 16 — STATUS: remaining

**Goal.** Detect public IP/port change (STUN mapped-address change
or consent fail). Re-gather, re-signal, re-check that NIC. Do not
flap on a single lost packet (T10 owns fail-vs-loss).

**Implement:** periodic STUN on the live socket; on mapping change,
restart ICE for that channel; signalling message `regather`.

**Verify:** lab: renew DHCP or switch wifi, expect re-nominate and
echo resumes. pytest with injected mapping change if feasible.

---

### T10 — Fail vs loss + recovery — sheet 17, 18 — STATUS: remaining

**Goal.** Consecutive check/consent failures = path **down**, not a
few lost `hello`s. Controlled retry of direct punch; if still dead,
TURN (T5). No unbounded flap (backoff + hold time).

**Implement:** down threshold, recovery probe, backoff yaml;
preserve echo API.

**Verify:** lab: drop the direct path (firewall), expect TURN or
recovery without treating 1–2 losses as down. pytest for the
threshold counter.

---

### T11 — mlink + ICE, one ISP — sheet (none) — STATUS: done

Not on the task sheet. Implement this when the user asks, **before
`T6`**. Do not renumber `T6`–`T10`. Do not implement `T6`–`T10` here.

**Goal.** One wifi NIC on each lab host. mlink carries teleop
control, then camera RTP, on the socket `T5` already nominates
(`path=direct` or `path=turn`). Tailscale leaves the data path.
SSH may still use Tailscale.

**Read:** this file (locked decisions 4, 5, 12, and the T11
diagram). `mlink-transport/config/lab-op-remote-laptop.yaml` and
`lab-edge-remote-laptop.yaml` (today’s Tailscale `ts` path).
`mlink-transport/docs/f18_end_to_end.md` (gripper over that path).
`video/README.md` (F8: RTP through mlink, WebRTC localhost).
`turn/agent` `run` / `ice_policy: all`. Repo
`IMPLEMENTATION.md` section 5 (ROS topics; do not invent a second
motion wire).

**Lab hosts.** Operator PC, role `controlling`, wifi `bind_ip`
(confirm with `ip -4 route get 1.1.1.1`; 2026-09-23 run
`192.168.222.56`). SO-ARM laptop, role `controlled`, wifi
`10.255.254.58`. Coturn `3.227.234.95` UDP `3478`, relay
`50000-50100`, user `labturn` (password only in gitignored yaml).
Signalling `ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765`.
Room `so-arm101` (do not reuse `hello-world` while a hello agent
might still be joined). Do not bind `tailscale0`. Do not use
`100.x` as bind, STUN, TURN, or peer.

**Implement:**

1. **Control, one path.** New mlink configs for this pair (do not
   rewrite Orin `lab-op.yaml` / `lab-edge.yaml`, and do not delete
   the Tailscale yaml). App face unchanged: operator
   `127.0.0.1:5501/5502`, laptop `127.0.0.1:5503/5504`.
2. **WAN is the nominated ICE connection.** Each daemon starts the
   existing ICE agent (`ice_policy: all`, coturn STUN and TURN
   set). Laptop joins `so-arm101` as `controlled` first; operator
   joins as `controlling`. After `connect()`, mlink send/recv for
   that single path uses that connection. On `path=turn`, aioice
   already encapsulates; do not put `3.227.234.95:<relay>` into
   mlink `peer:` and send raw UDP from a new socket. The relay port
   changes every run. On `path=direct`, send from the nominated
   local socket, not from a second bind.
3. **Bytes.** `/teleop/command` and `/teleop/heartbeat` operator →
   laptop. `/teleop/ack` and `/teleop/state` laptop → operator.
   Safety stays on the laptop. The 500 ms watchdog still stops the
   gripper when heartbeats stop. Browser stays on
   `http://127.0.0.1:8090/` and does not see the WAN address.
4. **Video, same socket, after control passes.** Laptop camera
   publishes H.264 RTP to `127.0.0.1:5004` (mlink-edge
   `listen_media`). mlink copies it on the **same** nominated
   socket. Operator mlink delivers `127.0.0.1:5004` to MediaMTX on
   this PC. Chrome plays `http://127.0.0.1:8889/cam` from the
   console. WebRTC ICE is localhost only
   (`webrtcAdditionalHosts: ["127.0.0.1"]`). Do not use
   `mediamtx.tailscale-lab.yml`. Do not point the console at
   `http://100.x:8889/cam`.
5. **Room.** One room, two ICE agents. The camera, MediaMTX, the
   browser, and the ROS nodes do not join. Signalling does not
   carry the heartbeat or RTP. Coturn carries those bytes only
   when the nominated path is `path=turn`.

**Do not:** `T6` NAT-TTL soak, `T7` second NIC or mlink bonding of
two ISPs, `T8`–`T10`, a second `paths:` entry, Tailscale as the
WAN, gathering on `tailscale0`, putting the camera in the room,
browser `iceServers` aimed at coturn, a new motion topic, F20
crypto. If the socket dies, the existing watchdog is the stop;
do not build rebind here.

**Verify:**

```bash
cd turn && python3 -m pytest -q
```

Pytest must still pass. Add a unit test that app datagrams cross a
stand-in nominated connection in both directions with no
`tailscale0` and no `100.x`. Skip live STUN/EC2 in pytest.

**Lab, control.** Coturn and signalling up. Both mlink daemons up.
Logs show one path, `ice_policy=all`, and `path=direct` or
`path=turn` (this pair has been `path=turn`: operator host →
laptop relay `3.227.234.95:<port>` in `50000-50100`). `ss` / the
path list shows no `tailscale0` and no `100.x`.

Open `http://127.0.0.1:8090/`. Heartbeats keep the watchdog quiet.
A short g/h jog moves Feetech id 6. Close the tab: the 500 ms
watchdog safe-stops the gripper.

**Lab, video (same session, same socket).** Console cam URL is
`http://127.0.0.1:8889/cam`, not a `100.x` host. The laptop camera
is visible in the tab. Media counters show RTP on that same
nominated peer.

**Lab result (2026-09-23).** Control passed. `g`/`h` moved Feetech id 6.
Video stayed on Tailscale (`?cam=http://100.120.193.52:8889/cam`); RTP
through mlink was not part of this run. Both daemons gathered host,
srflx, and relay and joined room `so-arm101`. Signalling exchanged
those candidates. Direct checks did not win. Nominated sends, both
`path=turn`:

- Operator relay `3.227.234.95:50014` → laptop relay `3.227.234.95:50071`
- Laptop relay `3.227.234.95:50071` → operator srflx `43.246.227.66:47832`

srflx was operator `43.246.227.66:47832` and laptop `86.98.43.27:21331`.
mlink `ice=up`, loss 0, heartbeat RTT about 390–400 ms. Steps:
`docs/t11_test_steps.md`. `commit: done`. Do not start `T6` until asked.

---

## Session prompts

Copy **one** prompt into a new Grok session. Do not give two
milestones to one session.

### T1

```text
Read /home/muhammadhassan/robots/turn/IMPLEMENTATION.md from the start.
Git branch: feature/turn-server.
T0 is the plan. Implement T1 only (skeleton + locked ICE design, sheet subtask 1).
Do not implement T2–T10. Do not edit mlink-transport/, teleoperation-prototype/, or video/.
Do not run coturn, STUN gather, or signalling.
Do not git commit unless I explicitly ask. Never git push.
When T1 works: set T1 STATUS done, keep commit remaining, list files, print T1 verify commands, stop.
```

### T2

```text
Read /home/muhammadhassan/robots/turn/IMPLEMENTATION.md from the start.
Also read turn/ as left by T1.
Git branch: feature/turn-server.
Implement T2 only (signalling, sheet subtask 5).
Do not gather STUN, do not ICE connect, do not TURN, do not mlink.
Do not git commit unless I explicitly ask. Never git push.
When T2 works: set T2 STATUS done, keep commit remaining, list files, print T2 verify commands, stop.
```

### T3

```text
Read /home/muhammadhassan/robots/turn/IMPLEMENTATION.md from the start.
Also read turn/ as left by T1–T2.
Git branch: feature/turn-server.
Implement T3 only (STUN gather, sheet subtasks 2 and 3). One NIC is enough.
Do not ICE connect, do not TURN Allocate, do not mlink.
Do not git commit unless I explicitly ask. Never git push.
When T3 works: set T3 STATUS done, keep commit remaining, list files, print T3 verify commands, stop.
```

### T4

```text
Read /home/muhammadhassan/robots/turn/IMPLEMENTATION.md from the start.
Also read turn/ as left by T1–T3.
Git branch: feature/turn-server.
Implement T4 only (punch, authenticated checks, nominate, echo — sheet 7, 8, 11).
Do not implement TURN fallback (T5). Do not mlink.
Do not git commit unless I explicitly ask. Never git push.
When T4 works: set T4 STATUS done, keep commit remaining, list files, print T4 verify commands including the two-NAT direct lab, stop.
```

### T5

```text
Read /home/muhammadhassan/robots/turn/IMPLEMENTATION.md from the start.
Also read turn/ as left by T1–T4.
Git branch: feature/turn-server.
Implement T5 only (TURN fallback, sheet 19). Run coturn; do not write a TURN server. No Twilio.
Do not start T6. Do not mlink.
Do not git commit unless I explicitly ask. Never git push.
When T5 works: set T5 STATUS done, keep commit remaining, list files, print T5 verify commands (force-relay lab + fallback lab), stop.
```

### T6

```text
Read /home/muhammadhassan/robots/turn/IMPLEMENTATION.md from the start.
Hello-world T1–T5 must already be STATUS done.
Implement T6 only (NAT type, mapping TTL, keepalives — sheet 4, 13, 14).
Do not git commit unless I explicitly ask. Never git push.
When T6 works: set T6 STATUS done, keep commit remaining, list files, print T6 verify steps, stop.
```

### T7

```text
Read /home/muhammadhassan/robots/turn/IMPLEMENTATION.md from the start.
Implement T7 only (channel/ISP tags + all NIC pairs — sheet 6, 10).
Cell/5G may be missing; do not block the milestone. Do not mlink bonding.
Do not git commit unless I explicitly ask. Never git push.
When T7 works: set T7 STATUS done, keep commit remaining, list files, print T7 verify steps, stop.
```

### T8

```text
Read /home/muhammadhassan/robots/turn/IMPLEMENTATION.md from the start.
Implement T8 only (source filter, quality metrics, expire, unauth — sheet 9, 12, 20, 21).
Do not git commit unless I explicitly ask. Never git push.
When T8 works: set T8 STATUS done, keep commit remaining, list files, print T8 verify steps, stop.
```

### T9

```text
Read /home/muhammadhassan/robots/turn/IMPLEMENTATION.md from the start.
Implement T9 only (NAT rebind + re-check — sheet 15, 16).
Do not git commit unless I explicitly ask. Never git push.
When T9 works: set T9 STATUS done, keep commit remaining, list files, print T9 verify steps, stop.
```

### T10

```text
Read /home/muhammadhassan/robots/turn/IMPLEMENTATION.md from the start.
Implement T10 only (path-fail vs loss + recovery — sheet 17, 18).
Do not git commit unless I explicitly ask. Never git push.
When T10 works: set T10 STATUS done, keep commit remaining, list files, print T10 verify steps, stop.
```

### T11

```text
Read /home/muhammadhassan/robots/turn/IMPLEMENTATION.md from the start.
Git branch: feature/turn-server.
T1–T5 are done. Implement T11 only (mlink + ICE, one wifi ISP: control, then video).
T11 is before T6. Do not renumber T6–T10. Do not implement T6, T7, T8, T9, or T10.
You may edit mlink-transport and the operator video/console path. Do not rewrite Orin lab-op.yaml / lab-edge.yaml.
Tailscale is SSH/mgmt only. Do not bind tailscale0 or use 100.x as a peer.
The camera, MediaMTX, the browser, and ROS do not join the signalling room. Chrome WebRTC stays on 127.0.0.1.
Do not git commit unless I explicitly ask. Never git push.
When T11 works: set T11 STATUS done, keep commit remaining, list files, print the T11 lab steps, stop.
```

---

## Out of this plan

- Second ISP, and the `T7` all-NIC matrix (mlink bonding of two nominated sockets is later)
- `T6` mapping TTL, `T9` rebind, `T10` fail-vs-loss (the robot watchdog covers a dead socket until those exist)
- Writing coturn / Twilio
- F16 console TLS, F20 mlink payload crypto
- Changing Linux default route; Tailscale as an ICE NIC
