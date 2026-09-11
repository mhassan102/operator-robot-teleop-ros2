# Stage 2 overview (localhost loopback)

Two OS processes on **this PC**. Real UDP on `127.0.0.1`. Two port-pairs
pretend to be eth and wifi. No real NICs, no `SO_BINDTODEVICE`.

`mlink-ping` talks only to the **app face**. Each daemon copies the
payload on every live path. The peer delivers the **first good** copy
and drops the rest. `--reflect` on edge sends the same way back; the
return path is **not** pinned to whichever path won inbound.

Edge app ports `:5503` / `:5504` are unused when edge is started with
`--reflect`.

---

## Processes and ports (both pictures combined)

```text
THIS PC — 127.0.0.1 only

┌──────────────┐      app face (not a bonded path)
│  mlink-ping  │
│              │  1 datagram
│  send ───────┼──────────────────────────────────────┐
│              │                                      ▼
│              │                        ┌─────────────────────────┐
│              │                        │        mlink-op         │
│              │                        │  listen_app :5501       │
│              │                        │  send_app   :5502       │
│              │                        │                         │
│              │                        │  eth  bind :41001       │
│              │                        │       peer :42001       │
│              │                        │  wifi bind :41002       │
│              │                        │       peer :42002       │
│              │                        └────────────┬────────────┘
│              │                                     │
│              │              copy seq=N on every live path
│              │                                     │
│              │            eth  src :41001 → dst :42001
│              │            wifi src :41002 → dst :42002
│              │                                     │
│              │                                     ▼
│              │                        ┌─────────────────────────┐
│              │                        │  mlink-edge [--reflect] │
│              │                        │  bind :42001 peer :41001│
│              │                        │  bind :42002 peer :41002│
│              │                        │  listen :5503 unused    │
│              │                        │  send   :5504 unused    │
│              │                        │                         │
│              │                        │  first good (seq=N)     │
│              │                        │  session.send() reflect │
│              │                        └────────────┬────────────┘
│              │                                     │
│              │              copy seq=M on every live path
│              │                                     │
│              │            eth  src :42001 → dst :41001
│              │            wifi src :42002 → dst :41002
│              │                                     ▼
│              │                        mlink-op first good (seq=M)
│  1 datagram  │                                      │
│  recv ◄──────┼──────────────────────────────────────┘
│              │                        send_app :5502
└──────────────┘
```

Same topology as a table:

| Role | Process | Socket | Bind (src) | Peer (dst) |
| ---- | ------- | ------ | ---------- | ---------- |
| app in | `mlink-op` | `listen_app` | `127.0.0.1:5501` | ping sends here |
| app out | `mlink-op` | `send_app` | — | `127.0.0.1:5502` (ping binds) |
| eth | `mlink-op` | path 0 | `:41001` | `:42001` |
| wifi | `mlink-op` | path 1 | `:41002` | `:42002` |
| eth | `mlink-edge` | path 0 | `:42001` | `:41001` |
| wifi | `mlink-edge` | path 1 | `:42002` | `:41002` |
| app in | `mlink-edge` | `listen_app` | `:5503` | unused with `--reflect` |
| app out | `mlink-edge` | `send_app` | — | `:5504` unused with `--reflect` |

The two virtual links are isolated by **port-pair**, not by NIC:

```text
eth  :  op :41001  ←UDP→  edge :42001
wifi :  op :41002  ←UDP→  edge :42002
```

A copy sent on eth cannot land on the wifi socket.

---

## Ping path (one datagram there and back)

Ping sends **one** payload into op. Op emits **two** copies (`seq=N`,
different `path_id`). Edge keeps the first, drops the second, then
reflects. Edge emits **two** copies (`seq=M`, independent data seq).
Op keeps the first and delivers **one** payload back to ping.

```text
mlink-ping                 mlink-op                    mlink-edge [--reflect]
    │                          │                              │
    │  1 datagram              │                              │
    │  127.0.0.1:5501          │                              │
    │─────────────────────────►│                              │
    │                          │  copy seq=N path=eth         │
    │                          │  src :41001 → dst :42001     │
    │                          │─────────────────────────────►│
    │                          │  copy seq=N path=wifi        │
    │                          │  src :41002 → dst :42002     │
    │                          │─────────────────────────────►│
    │                          │                              │
    │                          │                    first good (seq=N)
    │                          │                    later copy dropped
    │                          │                    session.send reflect
    │                          │                              │
    │                          │  copy seq=M path=eth         │
    │                          │  dst :41001 ← src :42001     │
    │                          │◄─────────────────────────────│
    │                          │  copy seq=M path=wifi        │
    │                          │  dst :41002 ← src :42002     │
    │                          │◄─────────────────────────────│
    │                          │                              │
    │                 first good (seq=M)
    │                 later copy dropped
    │  1 datagram              │                              │
    │  127.0.0.1:5502          │                              │
    │◄─────────────────────────│                              │
```

“Faster” is an **outcome**: both live paths get a copy; whichever
arrives first is delivered. v1 does not pick one path at send time.
Replies are duplicated independently; they are not pinned to the
inbound winner.

Kill-path demo: UDP `down eth` on op `--control` closes `:41001`.
Wifi (`:41002` ↔ `:42002`) still carries copies. Stream continues.
