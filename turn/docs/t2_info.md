# T2 signalling — what it is (and is not)

T2 is **only signalling**. It does not ICE-connect, punch, STUN, or TURN.

Later, **`server.py` and coturn run side-by-side on the same EC2 instance**:
`server.py` is our WebSocket matchmaker; **coturn** is the STUN/TURN daemon.
They are not the same process. We write `server.py`; we run coturn, we do not write it.

---

## T2 only (this milestone)

```text
  node A (operator)              EC2                     node B (robot)
  signalling_client.py           server.py               signalling_client.py
  ┌──────────────────┐         ┌─────────────┐         ┌──────────────────┐
  │ join room        │──WS───►│  room of 2  │◄──WS────│ join room        │
  │ send username,   │         │  forwards   │         │ send username,   │
  │ password,        │◄──WS────│  peer blob  │──WS────►│ password,        │
  │ candidate list   │         └─────────────┘         │ candidate list   │
  └──────────────────┘                                 └──────────────────┘
         ▲                                                      ▲
         │ later (not T2)                                       │ later (not T2)
         │ aioice agent                                         │ aioice agent
```

**Files**

- `signalling/server.py` — WebSocket room service. Agents `join` a room, then send ICE username, password, and candidates; the server forwards that blob to the other member. It does not punch, STUN, TURN, or carry `hello`.
- `agent/signalling_client.py` — agent side of that: connect, join, send your blob, wait for the peer’s blob. Same file on both ends (operator + robot), different `role` (`controlling` / `controlled`).

**Where they run**

- `server.py` — on EC2 in the lab (intended host). Right now pytest runs it on localhost.
- `signalling_client.py` — both ends, inside each ICE agent.

**Elastic IP** — not required to verify T2 (localhost pytest is enough). Needed when signalling (and later coturn) must be reachable from two NAT’d hosts (T3+ lab / T4–T5).

**aioice?** Yes on **both ends** (same library, different `role`). **No** on `server.py`. T2’s client only shuttles JSON; aioice gather/connect starts in T3/T4.

**Is `server.py` coturn?** No.

---

## Hello-world (T1–T5) — where the other terms sit

```text
                    EC2 Elastic IP
           ┌──────────────────────────────────┐
           │  server.py   WS signalling  T2   │  ← not coturn
           │  coturn      STUN :3478     T3   │
           │  coturn      TURN :3478     T5   │  ← coturn is this
           └──────────────┬─────────┬─────────┘
                          │         │
                    NAT   │         │   NAT
                          ▼         ▼
                 ┌────────────┐  ┌────────────┐
                 │ node A     │  │ node B     │
                 │ aioice     │  │ aioice     │
                 │ client.py  │  │ client.py  │
                 │ controlling│  │ controlled │
                 └────────────┘  └────────────┘

  T3 STUN:   each aioice asks coturn (or public STUN) "what is my public IP:port?"
  T2 signal: both send those candidates (+ ICE user/pass) through server.py
  T4 punch:  both aioice send authenticated checks to each other; if it works,
             nominate A↔B UDP (EC2 off the data path)
  T5 TURN:   if punch fails, both aioice Allocate on coturn; data is A→EC2→B
```

| Milestone | What happens |
| --------- | ------------ |
| T1 | Layout + locked design: punch first, TURN only if punch fails |
| T2 | Exchange ICE username, password, candidate list over WebSocket |
| T3 | STUN gather: mapped public IP:port on one NIC |
| T4 | Punch + authenticated checks + nominate direct UDP + echo `hello` |
| T5 | TURN fallback through coturn when punch fails |

---

## One-liners

| Term | One line |
| ---- | -------- |
| **ICE** | Protocol that tries addresses (candidates) until two peers can send UDP to each other. |
| **STUN** | Ask a public server “what public IP:port does my NAT give me?” |
| **Punch** | Both sides send UDP at those mapped ports at once so the NATs allow the path. |
| **TURN** | If punch fails, a relay (coturn) copies UDP A → server → B. |
