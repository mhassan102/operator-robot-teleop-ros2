# Fleet — implementation plan

Read this file first in a new session. The picture is
`fleet/ARCHITECTURE.md`. Implement **one** milestone, then stop.

Branch: `feature/fleet` at `81156a4`. The operator app is host
Python (`2605cfb`). It does not start Docker and it does not use
the ROS image. The robot app still starts its container. Do not
push. Do not start TURN milestones T7–T10. Do not commit unless
the user in that session explicitly asks.

Push happens once, after every milestone below is committed on
this branch. That push goes to GitLab remote `gitlab`
(`ssh://git@gitlab.serverwild.com:24/automotive-team/remote-teleops.git`).
It does not go to GitHub `origin`.

---

## How to use this file

**Planner / check-in session:** the user runs the manual test, then
comes back. After they say that milestone works:

1. Check the work against the contract below
2. Set that row `STATUS: done` and `commit: done`
3. Commit on `feature/fleet` (do not push)
4. Paste the next milestone's session prompt

**Implementation session:**

- Read this file from the start, then the sections of
  `fleet/ARCHITECTURE.md` named in that milestone, then only the
  files named there.
- Implement **exactly one** milestone (`F1` … `F5`). Stop even if
  the next one looks small.
- Do not re-open locked decisions.
- Do not start a `blocked` or `on hold` row.
- Do not `git commit` or `git push`.
- Do not SSH to the robot, the operator, or EC2. Do not start
  mlink, Docker, the camera, the console, or the arm.
- Do not rsync the registry to EC2. Do not restart signalling.
  EC2 stays on the current password registry until F5, and only
  the user does that restart.
- Do not change the status board. The planner does that after the
  user confirms the manual test.
- When the code works locally: list files changed, print the pytest
  command, then print manual test steps for **this PC**, the
  **robot PC**, and **EC2**. Write those steps in
  `fleet/docs/F<N>_usage.md`. Stop.

F0 is this plan. The next session implements F1.

F1, F2, and F3 must not change what the lab speaks today. Their
real-arm step is a regression of the current ID-and-password
teleop. F4 changes the apps, so those apps must not be installed
on the lab PCs. The first fleet drive of the real arm is F5, after
the user updates EC2.

---

## Status board

Update `STATUS` when a milestone finishes. Values: `remaining`,
`done`, `blocked`, `on hold`.

`commit`: `remaining` until the user asks to commit that milestone;
then `done`.

| ID | Milestone | STATUS | commit |
| -- | --------- | ------ | ------ |
| F0 | This plan | done | done |
| F1 | Registry directory beside the password login | remaining | remaining |
| F2 | Approve, attach, release, and stale | remaining | remaining |
| F3 | Robot identity file, wire unchanged | remaining | remaining |
| F4 | Fleet page and robot token on the wire | remaining | remaining |
| F5 | Release, sign out, and remove the password login | remaining | remaining |

**Next to implement:** F1. Sqlite accounts, token registration,
presence, and the fleet snapshot. The current robot-id login
stays.

**Not in F1–F5.** A hired-operator marketplace. An admin role on
the page. Revoking an approval from the dashboard. GPS. TLS on
the registry. A new EC2 port. An operator container. Rewriting
`operate.js`. Joints 1–5. A planner or MoveIt. TURN milestones
T7–T10. The one-notch gripper change already in `81156a4`.

---

## Locked decisions

The product rules are `fleet/ARCHITECTURE.md`, section
"Key Decisions". These rules bind every session:

1. **One milestone.** An implementation session does not start the
   next row.
2. **The operator process stays host Python.** No Docker and no
   ROS image on the operator PC. `OperatorHost` in
   `packaging/operator_app/host.py` serves the pages. The robot
   app still starts its container.
3. **TCP 8765 stays the only registry port.** `join` stays ICE.
   `register` and `login` enter the registry. No 8766, no listener
   on 3479, no listener in the relay range. Coturn is not edited.
4. **`"v": 1` stays.** The registry code stays importable on
   Python 3.9. No new pip dependency. `sqlite3` and `hashlib` are
   stdlib.
5. **The password path stays until F5.** A `register` with
   `password` and no `token` still replies exactly
   `{"v":1,"type":"registered"}` with no `approval` field. It does
   not write sqlite. A `login` with `robot_id` and no `username`,
   against an in-memory password session, still follows today's
   login. It does not receive `fleet`. `logout` still returns
   `logged_out` on that session until F5.
6. **A token row rejects a robot-id login.** That body is
   `legacy_login` and the socket closes. It does not attach. This
   is true from F1.
7. **Do not rsync EC2 before F5.** A half-updated lab fails closed
   or, worse, mixes the two logins. The user updates EC2 only when
   F5 is ready.
8. **Implementers do not drive the arm.** No serial port, no `g`
   or `h`, no mlink, no Docker, no SSH. Tests bind `127.0.0.1` on
   a free port. They do not bind 8090, 8765, 8766, 3479, or
   50000–50100.
9. **Gripper bytes and video stay off the registry.** Inventory
   is still collected fresh on `operator_attached` and is not
   stored.

---

## Milestone contracts

### F0 — This plan — STATUS: done

`fleet/ARCHITECTURE.md` and this file. `commit: done`.

---

### F1 — Registry directory beside the password login — STATUS: remaining

**Goal.** The signalling process can store operator accounts and
robots in sqlite, accept a device token, remember presence, and
push a fleet snapshot to a username login. The lab's current
password register and password login still pass their existing
tests, byte for byte.

**Read:** `fleet/ARCHITECTURE.md` sections "Durable file",
"Tables", "Who may write accounts", "Robot register", "Presence",
and "Operator login and the fleet push". Then
`packaging/registry/session.py` (`hash_password`,
`password_matches`, `_accept_register`, `_accept_login`, `_drop`)
and `turn/signalling/server.py` (`Hub.handle`, `_new_registry`,
`start_server`, `_amain`).

**Implement:**

- `packaging/registry/store.py`. The two tables in the
  architecture. `secret_hash` is `salt (16) || digest (32)`. New
  fleet rows use 200_000 PBKDF2 iterations. `password_matches`
  gains an `iterations` argument and still defaults to 20_000 so
  current unit tests pass. Fleet rows do not use 20_000.
- `packaging/registry/admin.py`, entry
  `python3 -m packaging.registry.admin`. Commands: `add-operator`,
  `set-password`, `disable-operator`, `enable-operator`,
  `list-operators`, `clear-robot`. Passwords via `getpass`, minimum
  8 characters. Usernames match
  `^[A-Za-z][A-Za-z0-9._-]{0,31}$`. `add-operator` does not replace
  an existing user. The CLI prints ids and usernames only, never a
  hash or a password. `PRAGMA busy_timeout=5000`.
- `Registry` opens one sqlite connection in `__init__` on the
  event-loop thread. `hash_password` and `password_matches` for
  register and login run in `asyncio.to_thread`. The coroutine
  applies the result before it touches sqlite or a socket.
  Importing the registry must not create the file.
- Database path. `start_server` keeps `:memory:` unless `db_path=`
  is passed. `_amain` uses `TELEOP_REGISTRY_DB` or
  `$HOME/.local/share/teleop/registry.sqlite`, directory `0700`,
  file `0600`. If that file cannot be opened, `_new_registry`
  returns `None` and logs the path. ICE `join` still runs.
  `register` and `login` then fall through to the ICE
  `unknown_type` error. Durable file uses WAL. `:memory:` skips WAL.
- Token register, as the architecture table. Unknown id inserts
  `approval=pending` unless 500 pending rows exist (`full`, no
  insert). Known id with the same token replaces `robot_ws` first,
  then closes the previous socket, and does not change approval or
  the hash. A wrong token is `auth` and does not replace the
  socket. Reply is
  `{"v":1,"type":"registered","approval":"pending"}` or `approved`.
  Closing a replaced socket must not pop the row and must not clear
  a hold. Password register with no token does not write sqlite and
  still replies exactly `{"v":1,"type":"registered"}`.
- Presence. Not relayed. Accepted only from the current robot
  socket. `phase` is `idle`, `starting`, `ready`, `stopped`, or
  `error`. Bad phase or a bad mode is `bad_field` and does not
  update the row. Sqlite is written when mode, location, hostname,
  or phase changes, and at most once every 30 seconds for
  `last_seen` alone. Unchanged presence does not push `fleet`.
- Username login. Success is `logged_in` with `username`, then one
  `fleet` snapshot. Wrong password, unknown user, and disabled are
  all `auth`. The socket stays open. A second live socket for that
  username is `already_attached` and is not logged in. Five
  failures lock that username until success, process restart, or a
  new hash from `set-password`. The fifth failure does not check
  the password.
- `fleet` goes only to sockets that completed a username login.
  Never send it to a password-session operator, and never place it
  beside `logged_in`, `inventory`, or `status` on that socket.
  Counts follow the architecture: `registered`, `autonomous`,
  `needs_manual`, and `in_teleop` do not double-count. Pending is
  not in those four. Rows sorted by `robot_id`. No token, hash, or
  password in the snapshot. Coalesce pushes to one snapshot per
  socket per 50 ms.
- A `login` with `robot_id` and no `username`, against a sqlite
  robot, is `legacy_login` and closes the socket. The same body
  against an in-memory password session with no sqlite row still
  uses today's login and does not close the socket.
- `approve`, `attach`, `release`, and `sign_out` are not this
  milestone. Reply `unknown_type` and leave the socket up.
- `turn/signalling/server.py` only so `_amain` opens the durable
  file and `start_server` stays on `:memory:`. Do not add an
  argument that existing `start_server(host, port)` callers must
  pass. Do not edit `mlink-transport/tests/test_ice_path.py` or
  `turn/tests/test_run.py`.

**Do not:** edit `packaging/robot_app/`, `packaging/operator_app/`,
or `teleoperation-prototype/web/`. Do not delete `logout`. Do not
make a password `registered` body grow an `approval` field. Do not
commit, push, SSH, or rsync. Do not edit the status board. Do not
start mlink, Docker, or the arm.

**Verify:**

```bash
cd /home/muhammadhassan/robots && PYTHONPATH=. python3 -m pytest -q packaging/tests turn/tests/test_signalling.py
```

**Lab:** write `fleet/docs/F1_usage.md`. EC2 is not updated. The
user's real-arm check is the current teleop: robot ID and password,
login, config, Start, one `g` or `h` with the gripper clear, Stop.
The implementer does not run it. The new directory is proven by
pytest, including a `tmp_path` database that survives a second
`Registry` open.

**Session prompt:**

```text
You are the implementer. Branch feature/fleet. Read
fleet/IMPLEMENTATION.md milestone F1 and fleet/ARCHITECTURE.md
sections Durable file, Tables, Who may write accounts, Robot
register, Presence, and Operator login and the fleet push.
Implement only F1.

Add a sqlite registry beside the current password login. New files
are packaging/registry/store.py and packaging/registry/admin.py.
Operator accounts and robot token rows live in sqlite. A token
register stores only a hash, replies registered with approval, and
does not let a wrong token replace the socket. Presence updates the
row and is not relayed. A username login receives logged_in and then
one fleet snapshot. A robot-id login against a sqlite robot is
legacy_login and closes the socket.

The current password register and password login must keep passing.
A password register replies exactly {"v":1,"type":"registered"} with
no approval field and writes no sqlite. A robot-id login against an
in-memory password session does not receive fleet and does not close
the socket. logout still returns logged_out on that session.
approve, attach, release, and sign_out are unknown_type. Leave the
socket up.

start_server stays on :memory: unless db_path= is passed. _amain
opens TELEOP_REGISTRY_DB or ~/.local/share/teleop/registry.sqlite.
If that file cannot be opened, ICE join still runs. Do not edit the
robot app, the operator app, or the web pages. Do not edit
mlink-transport tests or turn/tests/test_run.py.

Python 3.9. No new pip dependency. Hashing runs in
asyncio.to_thread, not on the ICE loop. Secrets must not appear in
outbound frames or admin output.

Do not commit or push. Do not SSH. Do not rsync EC2. Do not restart
signalling. Do not start mlink, Docker, the camera, or the arm. Do
not send g or h. Do not change the status board. When F1 works, list
files, print the pytest command, print manual steps for this PC, the
robot PC, and EC2, and stop. Write those steps in
fleet/docs/F1_usage.md. The real-arm check is the current ID and
password teleop, one tap of g or h after the gripper is clear. The
implementer does not run it. EC2 is not updated.
```

---

### F2 — Approve, attach, release, and stale — STATUS: remaining

**Goal.** A logged-in operator can approve a robot and attach to
one of them. The existing inventory, config, start, stop, and
status relay still runs after attach. Release and sign out exist
on the socket. The password login tests still pass.

**Read:** `fleet/ARCHITECTURE.md` section "Approve, attach,
release, sign out", the stale sweep, and the count rules. F1's
code in `packaging/registry/`.

**Implement:**

- `approve`, `attach`, `release`, `sign_out` as the architecture
  specifies. Approve works for an offline pending robot and stays
  approved across a new `Registry` on the same file. Attach of a
  pending robot is `pending`. Unknown id is `not_found`. Offline
  is `offline`. Another username is `busy`. A second robot on the
  same socket is `already_attached`.
- Success sends `attached`, then `operator_attached` to the robot.
  Inventory is still not stored. `config`, `start`, `stop`,
  `inventory`, `config_ok`, `status`, and `error` still relay only
  while this operator holds that robot. `status` updates the stored
  phase so a fresh `ready` can count as `in_teleop` with status
  `running`.
- `release` clears the hold, sends `operator_detached`, replies
  `released`, and leaves the operator logged in. `sign_out`
  releases first if needed, replies `signed_out`, and does not
  delete the operator row.
- Stale sweep. One task, every 1 second, sqlite robots only. A
  missing `last_seen` is not stale. An unheld robot older than 30
  seconds is offline and gets no `stop`. A held robot gets
  `{"v":1,"type":"error","code":"stale"}`, stays `in_teleop`, and
  the hold remains so `stop` can still be forwarded. After 30
  seconds without `stopped`, the registry forwards `stop`, then
  clears the hold. A real socket drop is `offline` and does not
  forward `stop`. A password session is not swept and is not sent
  `fleet`.
- `_maintain` also re-reads accounts. `disable-operator` or
  `set-password` drops that username's socket within about one
  second. `clear-robot` closes the robot socket, tells the operator
  `offline`, and pushes `fleet`.
- Pending rows with no socket and `last_seen` older than 7 days are
  deleted on process start. Approved rows are not.

**Do not:** change the robot app or the operator pages. Do not make
`logout` return `unknown_type`. Password sessions still get
`logged_out`. Do not rsync EC2. Do not commit or push. Do not edit
the status board. Do not drive the arm.

**Verify:** the same pytest command as F1.

**Lab:** write `fleet/docs/F2_usage.md`. EC2 is not updated. The
real-arm check is again the current ID-and-password teleop, one
`g` or `h`. The new messages are proven by pytest. The implementer
does not run the arm.

**Session prompt:**

```text
You are the implementer. Branch feature/fleet. Read
fleet/IMPLEMENTATION.md milestone F2 and the approve, attach,
release, sign out, and stale sections of fleet/ARCHITECTURE.md.
Implement only F2. F1 is already in the tree.

A username login can approve a robot and attach to one online
approved robot. Attach sends operator_attached. The existing
inventory, config, start, stop, and status frames still relay only
for that pair. release keeps the operator logged in. sign_out does
not delete the account. The 30 second stale sweep matches the
architecture: stale while the socket is still open, offline only
when the socket is gone. disable-operator, set-password, and
clear-robot take effect from the one-second maintain task.

Do not change the robot app or the operator pages. Password register
and password login stay as F1 left them. logout on a password
session still returns logged_out. Do not rsync EC2. Do not commit or
push. Do not SSH. Do not start mlink, Docker, or the arm. Do not
send g or h. Do not change the status board.

When F2 works, list files, print the pytest command, and write
fleet/docs/F2_usage.md with steps for this PC, the robot PC, and
EC2. The real-arm check is the current teleop, one tap of g or h.
The implementer does not run it. EC2 is not updated.
```

---

### F3 — Robot identity file, wire unchanged — STATUS: remaining

**Goal.** A robot can load a stable id and a device token from a
`0600` file. `launch` does not use that file yet. The lab robot
still prints a new id and still asks for a password.

**Read:** `fleet/ARCHITECTURE.md` section "Robot app" through the
identity file. `packaging/robot_app/credentials.py` and `cli.py`
(`new_id`, `prompt_password`, `launch`).

**Implement:**

- `load_or_create(path)` in `credentials.py`. Missing path: mint a
  9-digit id with `new_id()` and a token with `secrets.token_hex(32)`,
  write JSON, mode `0600`. Existing good file: return the same id
  and token, and do not print the token. A directory, unreadable
  file, bad JSON, a bad id, or an empty token exits non-zero and
  does not replace the file.
- Unit tests in `packaging/tests/test_robot_credentials.py`.
- Do not edit `cli.py` or `session.py`. `launch` still calls
  `new_id()` and `prompt_password`. `run_robot_session` still
  password-registers.

**Do not:** change the operator app, the registry, or the web
pages. Do not commit or push. Do not drive the arm.

**Verify:** the same pytest command as F1.

**Lab:** write `fleet/docs/F3_usage.md`. The robot terminal still
asks for a password. The real-arm check is the current teleop.
The file helper is proven by pytest. The implementer does not run
the arm and does not rsync EC2.

**Session prompt:**

```text
You are the implementer. Branch feature/fleet. Read
fleet/IMPLEMENTATION.md milestone F3 and the robot identity file
in fleet/ARCHITECTURE.md. Implement only F3.

Add load_or_create to packaging/robot_app/credentials.py. A missing
file mints a 9-digit id and a token, mode 0600. A good file returns
the same pair and does not print the token. A bad file exits
non-zero and is not replaced. Tests go in
packaging/tests/test_robot_credentials.py.

Do not edit cli.py or session.py. launch still calls new_id and
prompt_password. The wire format stays the password register. Do not
edit the operator app or the registry. Do not commit or push. Do not
SSH. Do not rsync EC2. Do not start mlink, Docker, or the arm. Do
not send g or h. Do not change the status board.

When F3 works, list files, print the pytest command, and write
fleet/docs/F3_usage.md. The real-arm check is the current teleop,
one tap of g or h. The implementer does not run it.
```

---

### F4 — Fleet page and robot token on the wire — STATUS: remaining

**Goal.** The robot registers with the token file. The operator
logs in with a username and password, sees `/fleet`, and Operate
opens the existing config page and `/operate`. Logout on the page
still sends `logout` until F5. Do not install this build on the
lab PCs. EC2 still speaks the password login.

**Read:** `fleet/ARCHITECTURE.md` section "1. Operator app", and
`packaging/operator_app/host.py`, `console.py`, `login.py`,
`session.py`, plus `teleoperation-prototype/web/login.html`,
`login.js`, `config.html`, `session_bar.js`.

**Implement:**

- `launch` uses `load_or_create` at `~/.config/teleop/robot.json`.
  No password prompt. The terminal prints the id and not the token.
  `--mode manual|autonomous` and `--location` are the presence
  fields. Default mode is `manual`. Blank location displays as the
  hostname. `run_robot_session` sends `token` and does not send
  `password`. Presence goes out after `registered` and every 10
  seconds. `n` no longer sets a password.
- `/api/login` posts `username` and `password`. After `logged_in`,
  `view` is `fleet` and the window opens `/fleet`. New files
  `teleoperation-prototype/web/fleet.html` and `fleet.js`, styled
  from `operate.css`. The page shows the four counts and a table
  of ID, location, status, mode, and action. Approve posts
  `/api/approve`. Operate posts `/api/attach`. The browser does not
  open TCP 8765. A refresh reads `/api/session` and does not send
  login again.
- After `attached`, wait for inventory, then open `/config`. Review
  and Start stay the current host path. Start still calls the
  in-process helper for mlink only and must refuse
  `start_operator_mlink.sh`. Heartbeats arm only after mlink-op is
  up. `/operate` and `operate.js` stay as they are.
- The session bar still has Logout, and logout still sends
  `{"v":1,"type":"logout"}`. Do not add Release or Sign out in this
  milestone. That is F5.
- Tests that construct `RobotState` or expect `password` in
  `register_message` are updated here. The login test covers a
  token register, `add-operator`, username login, approve, and
  attach. `test_stop_keeps_config_until_logout` expects
  `operator_detached` and a cleared accepted config, and it does
  not send `logout`. Password-register replies inside
  `test_registry.py` stay the old body with no `approval` field,
  because the scaffold is still there for any leftover password
  client.

**Do not:** rsync EC2. Do not install this operator or this robot
app on the lab machines. The current EC2 process rejects a token
register. Do not add Docker or ROS to the operator package. Do not
rewrite `operate.js`. Do not commit or push. Do not drive the arm.
Do not edit the status board.

**Verify:** the same pytest command as F1. It must pass without
PyQt installed.

**Lab:** write `fleet/docs/F4_usage.md`. The steps say not to
replace the lab apps and not to restart EC2. The user's check is
the previous build, still the ID-and-password teleop. The new page
is proven by pytest.

**Session prompt:**

```text
You are the implementer. Branch feature/fleet. Read
fleet/IMPLEMENTATION.md milestone F4 and section "1. Operator app"
in fleet/ARCHITECTURE.md. Implement only F4. F1, F2, and F3 are in
the tree.

The robot launch uses ~/.config/teleop/robot.json from
load_or_create. It prints the id, does not print the token, and does
not ask for a password. It registers with token, mode, and location,
and it sends presence every 10 seconds.

The operator login posts username and password. After logged_in the
window opens /fleet. Add fleet.html and fleet.js. Show the four
counts and the robot table. Approve and Operate call the registry
through ConsoleApp. Operate then uses the existing /config page and,
after Start, /operate. Do not rewrite operate.js. Do not start an
operator container. Start must refuse start_operator_mlink.sh.

Logout still sends {"v":1,"type":"logout"}. Do not add Release or
Sign out. That is F5. Do not delete the password scaffold in the
registry. test_registry.py still expects a password register to
reply {"v":1,"type":"registered"} with no approval field.

Do not rsync EC2. Do not install this build on the lab PCs. Do not
commit or push. Do not SSH. Do not start mlink, Docker, or the arm.
Do not send g or h. Do not change the status board. Tests must pass
without PyQt.

When F4 works, list files, print the pytest command, and write
fleet/docs/F4_usage.md. Those steps tell the user not to replace the
lab apps. The real-arm check stays the previous ID-and-password
teleop. The implementer does not run it.
```

---

### F5 — Release, sign out, and remove the password login — STATUS: remaining

**Goal.** The page can leave a robot without dropping the operator
login, and the password login is gone. This is the first build the
user may put on the lab, together with an EC2 signalling restart.

**Read:** `fleet/ARCHITECTURE.md` session bar, Release, Sign out,
Quit, `stale`, and `offline`. F4's `console.py` and `session_bar.js`.

**Implement:**

- Stop still returns to `/config` and leaves the operator process
  up. Release stops mlink first if a drive is up, sends `release`,
  and returns to `/fleet` with the login held. Sign out does that
  stop when a robot is held, sends `sign_out`, and returns to `/`.
- Quit stops mlink, then the robot, then exits the process. It
  does not send `sign_out` and it does not run `docker compose`.
  Closing the desktop window is that same Quit.
- `offline` during a drive stops local mlink only and returns to
  `/fleet`. `stale` still sends `stop` because the registry socket
  is open, then returns to `/fleet`.
- Delete the password scaffold. A password `register` is
  `legacy_robot` and closes the socket. Every robot-id `login` is
  `legacy_login` and closes the socket. `logout` is `unknown_type`.
  The tests that expect the old `registered` body and the logout
  button are rewritten here, not before.
- `fleet/docs/F5_usage.md` is the lab procedure: rsync
  `packaging/` and `turn/signalling/server.py` the way
  `packaging/docs/P3_usage.md` does, set `TELEOP_REGISTRY_DB` to
  `~/.local/share/teleop/registry.sqlite`, restart only the
  process `ss -ltnp` shows on TCP 8765, run `add-operator`, start
  the SO-ARM robot with `--mode manual`, approve it, start a second
  robot process with `--mode autonomous` and its own config path,
  approve it, Operate the manual robot, one `g` or `h`, Release,
  Sign out. Rollback is `81156a4`. The implementer writes these
  steps and does not run them. The user runs them after the
  gripper is clear. Do not restart signalling while an arm is in a
  drive.

**Do not:** commit or push. Do not SSH. Do not restart EC2
yourself. Do not drive the arm. Do not edit the status board. Do
not add an operator container.

**Verify:** the same pytest command as F1. The suite is green on
the deployed protocol. A password register is no longer a passing
login.

**Session prompt:**

```text
You are the implementer. Branch feature/fleet. Read
fleet/IMPLEMENTATION.md milestone F5 and the Release, Sign out,
Quit, stale, and offline parts of fleet/ARCHITECTURE.md. Implement
only F5. F4 is already in the tree.

Stop returns to /config and leaves the operator process up. Release
returns to /fleet and keeps the login. Sign out returns to /. Quit
stops mlink, then the robot, then exits the process. Closing the
desktop window is Quit. There is no docker compose step. offline
during a drive stops local mlink only. stale still sends stop.

Then delete the password scaffold. Password register is legacy_robot
and closes the socket. Every robot-id login is legacy_login and
closes the socket. logout is unknown_type. Rewrite the tests that
still expect the old registered body or the Logout button. Do this
in this milestone, not before.

Do not commit or push. Do not SSH. Do not rsync or restart EC2
yourself. Do not start mlink, Docker, or the arm. Do not send g or
h. Do not change the status board.

When F5 works, list files, print the pytest command, and write
fleet/docs/F5_usage.md. Those steps are the first real fleet lab:
EC2 rsync, one signalling restart, add-operator, one manual robot,
one autonomous robot, approve, Operate, one tap of g or h, Release,
Sign out, and rollback to 81156a4. The implementer does not run
them. The user does, after the gripper is clear. Do not restart
signalling during a drive.
```

---

## Lab safety (every milestone)

The person at the robot watches the arm. The first fleet motion is
one tap of `g` or `h` with the gripper clear, and only in the F5
lab, after the HUD says CONNECTED. F1 through F4 do not replace
the lab apps. Stop brings mlink down before the robot stack. Quit
and closing the operator window do that stop and then exit the
operator process. Packaging and fleet sessions do not run that
lab. The user does.
