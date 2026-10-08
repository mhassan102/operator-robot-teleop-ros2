# Desktop packaging

Operator and robot apps pair through the signalling process that
already listens on **TCP 8765**. Coturn is a different service
(UDP 3478, relay ports UDP 50000–50100) and this tree does not
change it.

The first WebSocket message chooses the path:

| First `type` | Path |
| --- | --- |
| `join` | Existing ICE room (candidates exchanged as before) |
| `register` | Robot terminal. A device token is hashed and stored. A password register is `legacy_robot` and the socket closes. |
| `login` | Operator desktop app. Username and password. A robot-id login is `legacy_login` and the socket closes. |

A later `register` or `login` on a connection that already joined an
ICE room stays on the ICE path. A bad first frame does too.

The message list is in `IMPLEMENTATION.md`. The registry checks
`v` and `type`, then relays:

| From the robot | From the operator |
| --- | --- |
| `inventory`, `config_ok`, `status`, `error` | `config`, `start`, `stop` |

`logout` is `unknown_type`. Release detaches the operator and replies
`released`. The operator socket stays logged in. Sign out replies
`signed_out` and does not delete the account. Closing the operator
socket detaches the same way as release.

It does not read the fields inside the relayed messages, and it does
not carry gripper bytes or video. Passwords and device tokens are not
written to logs. Approval and token hashes are in sqlite. Live sockets
are memory only. A new token `register` for an ID replaces the previous
robot socket and keeps the stored hash. `logged_in` for an operator is
`{"v":1,"type":"logged_in","username":"<name>"}`.

Reply codes include `bad_id`, `auth`, `busy`, `offline`, `legacy_robot`,
and `legacy_login`. A malformed frame is `bad_json`. A frame whose `v`
or `type` is not part of this session is `unknown_type`, including
`logout`. If the robot socket drops, the operator is sent `offline`.
The robot row stays.

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
