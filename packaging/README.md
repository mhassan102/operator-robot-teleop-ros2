# Desktop packaging

Operator and robot apps pair through the signalling process that
already listens on **TCP 8765**. Coturn is a different service
(UDP 3478, relay ports UDP 50000–50100) and this tree does not
change it.

The first WebSocket message chooses the path:

| First `type` | Path |
| --- | --- |
| `join` | Existing ICE room (candidates exchanged as before) |
| `register` | Robot terminal (SSH, no Qt). The registry stores a password hash. |
| `login` | Operator desktop app. One operator per robot. |

A later `register` or `login` on a connection that already joined an
ICE room stays on the ICE path. A bad first frame does too.

The message list is in `IMPLEMENTATION.md`. The registry checks
`v` and `type`, then relays:

| From the robot | From the operator |
| --- | --- |
| `inventory`, `config_ok`, `status`, `error` | `config`, `start`, `stop` |

It does not read the fields inside those messages, and it does not
carry gripper bytes or video. Passwords are not written to logs.
Sessions are memory only: a new `register` for an ID replaces the
previous robot socket and the stored hash. `logged_in` is
`{"v":1,"type":"logged_in","hostname":"<name from register>"}`.

Reply codes: `bad_id`, `auth`, `busy`, `offline`. A malformed frame
is `bad_json`. A frame whose `v` or `type` is not part of this
session is `unknown_type`. If the robot socket drops, the operator
is sent `offline` and the session is forgotten.

Tests bind `127.0.0.1` on a free port. They do not bind 8766, 3479,
or 50000–50100.

```bash
cd /home/muhammadhassan/robots && PYTHONPATH=. python3 -m pytest -q packaging/tests/test_registry.py turn/tests/test_signalling.py
```

From this directory, `python3 -m pytest -q` runs the packaging tests.

Local server (loopback only):

```bash
cd /home/muhammadhassan/robots
PYTHONPATH=. python3 -m turn.signalling.server --bind 127.0.0.1 --port 8765
```
