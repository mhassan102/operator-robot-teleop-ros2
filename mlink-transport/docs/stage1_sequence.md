# Stage 1 sequence (protocol library)

Stage 1 is **not** client/server processes. It is one shared Python
module (`mlink-transport/proto/`). Tests create two `MlinkSession`
objects (A and B) on the same process and wire them through
`FakeNetwork` (in-memory UDP). Later, `mlink-op` and `mlink-edge`
will each own one session and import this same module.

Default lab picture used below: two paths, `eth` and `wifi`.

```text
pytest / caller
   │
   ├── MlinkSession A          MlinkSession B
   │     path eth  ──────────►   path eth
   │     path wifi ──────────►   path wifi
   │              FakeNetwork
```

---

## 1. App datagram: copy on every live path, first-good wins

Same payload, same `session` + `seq`, different `path_id`. B delivers
the first good copy and drops the rest. No reorder hold.

```mermaid
sequenceDiagram
    autonumber
    participant Caller
    participant A as MlinkSession A
    participant Sched as scheduler.eligible_paths
    participant Net as FakeNetwork
    participant B as MlinkSession B
    participant Dedupe as DedupeWindow (B)

    Caller->>A: send(payload)
    A->>A: enqueue (control queue first)
    A->>A: flush → _emit
    A->>A: seq += 1  (data seq only)
    A->>Sched: which paths are up and loss ≤ 20%
    Sched-->>A: [eth, wifi]

    Note over A,Net: copy 1: path_id=eth, seq=N
    A->>Net: eth.sendto(header+payload → B.eth)
    Net->>B: B.eth inbox

    Note over A,Net: copy 2: path_id=wifi, seq=N (same payload)
    A->>Net: wifi.sendto(header+payload → B.wifi)
    Net->>B: B.wifi inbox

    Caller->>B: poll()
    B->>B: recvfrom every path socket
    B->>Dedupe: observe(seq=N)  first copy
    Dedupe-->>B: deliver
    B-->>Caller: [payload]
    B->>Dedupe: observe(seq=N)  second copy
    Dedupe-->>B: duplicate → drop
```

If `wifi` is marked down or its loss is over the threshold, `_emit`
never sends a data copy on wifi. Eth still delivers.

---

## 2. Heartbeat and RTT (not delivered to the app)

Every 100 ms, each **up** path sends a heartbeat. Heartbeats use a
per-path counter, not the data `seq`. The peer echoes the original
timestamp; the sender measures RTT. Echoes are not re-echoed.

```mermaid
sequenceDiagram
    autonumber
    participant Clock as FakeClock
    participant A as MlinkSession A
    participant Net as FakeNetwork
    participant B as MlinkSession B

    Clock->>A: tick()  (advance ≥ 100 ms)
    A->>A: path eth still up
    A->>Net: HB(path=eth, hb_seq=k, ts=T)
    Net->>B: B.eth inbox

    Note over B: poll() — not app data
    B->>B: mark eth heard, up=true
    B->>B: observe_heartbeat(k)
    B->>Net: echo(HB|ECHO, seq=k, ts=T)
    Net->>A: A.eth inbox

    A->>A: poll()
    A->>A: observe_rtt(now - T)
    Note over A,B: payload is empty; nothing returned to the caller
```

Same exchange runs independently on wifi.

---

## 3. Path down, then probe brings it back

No data or heartbeat on a path for 300 ms → mark down, stop data
copies there, probe at 1 Hz. A heard probe/heartbeat marks the path
up again.

```mermaid
sequenceDiagram
    autonumber
    participant Clock as FakeClock
    participant A as MlinkSession A
    participant Net as FakeNetwork
    participant B as MlinkSession B

    Note over Net: wifi link set_down (or silence)
    Clock->>A: tick() after 300 ms silence on wifi
    A->>A: wifi.up = false
    Note over A: later send() copies data on eth only

    Clock->>A: tick() after 1 s
    A->>Net: PROBE|HB on wifi
    Net-->>A: dropped (link still down)

    Note over Net: wifi set_up again
    Clock->>A: tick()
    A->>Net: PROBE|HB on wifi
    Net->>B: B.wifi inbox
    B->>B: mark wifi heard, up=true
    B->>Net: echo probe
    Net->>A: A.wifi inbox
    A->>A: mark wifi heard, up=true
    Note over A: next send() copies data on eth and wifi again
```

---

## 4. Lossy path excluded from data (still heartbeats)

Scheduler v1: data copies go to every path that is **up** and
`loss ≤ 0.20`. Loss is inbound heartbeat loss over the last 20
heartbeats. A lossy-but-up path still gets heartbeats; it does not
get app payloads.

```mermaid
sequenceDiagram
    autonumber
    participant Caller
    participant A as MlinkSession A
    participant Sched as eligible_paths
    participant Net as FakeNetwork
    participant B as MlinkSession B

    Note over Net: ~30% of heartbeats toward A.eth dropped
    loop heartbeats
        A->>Net: HB on eth and wifi
        Net--xA: some eth echoes missing
        A->>A: eth.loss() > 0.20, wifi.loss() ≤ 0.20
    end

    Caller->>A: send(payload)
    A->>Sched: eligible?
    Sched-->>A: [wifi]  (eth skipped)
    A->>Net: data copy on wifi only
    Net->>B: B.wifi inbox
    B-->>Caller: poll() → [payload]
```

---

## What this is not

No `mlink-op` / `mlink-edge` processes, no real UDP, no app-facing
`127.0.0.1` sockets. Those appear in Stage 2: two OS processes, each
holding one `MlinkSession`, talking over real localhost UDP.
