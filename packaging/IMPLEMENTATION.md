# Desktop packaging — implementation plan

Read this file first in a new session. The picture is
`packaging/ARCHITECTURE.md`. Implement **one** milestone, then stop.

Branch: `feature/packaging`. Do not push. Do not start TURN
milestones T7–T10. Do not commit unless the user in that session
explicitly asks.

---

## How to use this file

**Planner / check-in session:** the user runs the manual test, then
comes back. After they say that milestone works:

1. Check the work against the contract below
2. Set that row `STATUS: done` and `commit: done`
3. Commit on `feature/packaging` (do not push)
4. Paste the next milestone's session prompt

**Implementation session:**

- Read this file from the start, then `packaging/ARCHITECTURE.md`,
  then only the files named in that milestone.
- Implement **exactly one** milestone (`P1` … `P8`). Stop even if
  the next one looks small.
- Do not re-open locked decisions.
- Do not start a `blocked` or `on hold` row.
- Do not `git commit` or `git push`.
- Do not SSH to the robot, the operator, or EC2. Do not start
  mlink, Docker, the camera, the console, or the arm.
- Do not change the status board. The planner does that after the
  user confirms the manual test.
- When the code works locally: list files changed, print the pytest
  command, then print manual test steps for **this PC**, the
  **robot PC**, and **EC2**. Stop.

P0 is this plan. The next session implements P1.

---

## Status board

Update `STATUS` when a milestone finishes. Values: `remaining`,
`done`, `blocked`, `on hold`.

`commit`: `remaining` until the user asks to commit that milestone;
then `done`.

| ID | Milestone | STATUS | commit |
| -- | --------- | ------ | ------ |
| P0 | This plan and the architecture | done | done |
| P1 | Registry (ID and password pairing) | done | done |
| P2 | Robot terminal: ID, password, inventory | done | done |
| P3 | Operator window: login | done | done |
| P4 | Operator config page | remaining | remaining |
| P5 | Serial port and camera arguments | remaining | remaining |
| P6 | Robot supervisor (start and stop) | remaining | remaining |
| P7 | Operator supervisor and in-app console | remaining | remaining |
| P8 | Debian packages | remaining | remaining |

**Next to implement:** `P4`.

**Not in P1–P8.** Bonding Interface 2 into mlink. Moving camera RTP
onto the mlink socket. Joints 1–5. Rewriting `operate.js`. Publishing
the ROS image inside the `.deb`. TLS on the registry. TURN milestones
T7–T10.

---

## Locked decisions

1. **Two programs, one desktop UI.** `teleop-robot` on the Ubuntu
   24.04 robot PC is a terminal process. Start it over SSH on
   Tailscale. It prints the ID and password and sends inventory
   after login. It has no Qt window. `teleop-operator` on the
   Ubuntu 22.04 operator PC is the only desktop UI. Login uses the
   signalling process already bound to **TCP 8765** on the existing
   EC2 host. There is no TCP 8766, and login does not use UDP 3479
   or UDP 50000–50100. Coturn stays UDP 3478, allocating relays
   from UDP 50000–50100. A `join` message stays the ICE path.
   `register` is the robot terminal. `login` is the operator UI.
   Tests bind `127.0.0.1` only.
2. **Language.** Python 3.9-compatible registry (EC2 is 3.9).
   The operator UI uses distro Python 3.10 and **PyQt5** from apt.
   The robot terminal uses distro Python 3.12 and does not import
   PyQt. Neither side uses Miniforge or a pip install into system
   Python. Tests are pytest. Later milestones do not add a robot GUI.
3. **The registry pairs the apps and relays session JSON.** Gripper
   bytes and video do not pass through it. A registry disconnect
   after `ready` does not stop the arm. Closing the operator window
   stops the operator stack so the existing 500 ms watchdog can
   torque the gripper off.
4. **ID and password are generated when the robot app starts.**
   ID is 9 digits. Password is 8 characters from
   `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`. The registry stores a hash.
   Passwords are not written to git, logs, or this file.
5. **Link is one of `tailscale` or `turn`.** The supervisor passes
   `--remote-laptop` or `--ice`. It never passes both. Interface 2
   set to anything other than None refuses Start in this slice.
6. **Interface 1** is a robot NIC that has the default route. For
   `turn`, its IPv4 is `bind_ip` in a generated runtime copy of the
   gitignored ICE yaml. For `tailscale`, mlink keeps `tailscale0`.
   Never write a `100.x` address into `bind_ip`. Hide `lo`,
   `tailscale0`, docker bridges, and veth from the dropdowns.
7. **Arm and video come from the robot inventory.** Default arm is
   the follower by-id `usb-1a86_USB_Single_Serial_5B61033180` when
   present. Default video is the capture node whose name contains
   `USB2.0_CAM1`. Metadata nodes are not listed. The leader serial
   may be listed and is not the default.
8. **Existing processes stay the motion path.** The supervisor
   spawns the current scripts. It does not publish ROS topics and
   does not add a second safety path.
9. **Git `video/start.sh` is the Orin encoder.** Leave it. The
   SO-ARM launcher is added beside it (`video/so-arm/`). It uses
   `x264enc` and MediaMTX, matching the script that already runs
   on the Dubai laptop.
10. **Do not rewrite** `mlink-transport/config/lab-op.yaml`,
    `lab-edge.yaml`, or `turn/` agent code. P1 may teach
    `turn/signalling/server.py` to hand `register` / `login` /
    inventory / config / start / stop / status to the packaging
    registry. ICE `join` and candidate exchange stay as they are.
    Generated yaml goes in a runtime directory and is gitignored.
11. **Implementer sessions do not drive the arm.** They never start
    the Feetech driver, never open `/dev/ttyACM0`, and never send
    `g` or `h`. P4 only reviews config. P5 is parse-only. P6 plans
    commands under `TELEOP_SUPERVISOR_DRY_RUN=1` and does not exec
    them. The first lab that may start the real driver is P7, and
    only the user runs it, after the gripper is clear on the table.
    That test is one tap of `g` or `h`. Each implementer writes the
    steps in `packaging/docs/P<N>_usage.md` and does not run them
    on the robot.
12. **Packages.** `teleop-robot` and `teleop-operator`, amd64 `.deb`,
    files under `/opt/teleop`. Only the operator package has a
    `.desktop` launcher and depends on PyQt5. The robot package is
    the terminal command. The ROS image is a host prerequisite, not
    a payload of the package.

---

## Protocol (P1 creates this; later milestones use it)

Every message is one JSON object with `"v": 1` and `"type"`.

Robot to registry:

```json
{"v":1,"type":"register","robot_id":"123456789","password":"AB23CD45","hostname":"AUTOOS-DEV-MUHAMMADOSAMA"}
```

Registry to robot: `{"v":1,"type":"registered"}` or
`{"v":1,"type":"error","code":"bad_id"}`.

Operator to registry:

```json
{"v":1,"type":"login","robot_id":"123456789","password":"AB23CD45"}
```

Success: `{"v":1,"type":"logged_in"}`. Failure:
`{"v":1,"type":"error","code":"auth"}`. A second operator while one
is attached: `{"v":1,"type":"error","code":"busy"}`. Unknown ID:
`{"v":1,"type":"error","code":"offline"}`.

After login the registry tells the robot
`{"v":1,"type":"operator_attached"}` and relays the messages below
from one socket to the other. It does not interpret them beyond
checking `v` and `type`.

Inventory (robot → operator):

```json
{"v":1,"type":"inventory",
 "interfaces":[{"name":"wlp0s20f3","ipv4":"10.255.254.58","up":true,"default_route":true}],
 "arms":[{"path":"/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B61033180-if00","tty":"/dev/ttyACM0","label":"follower"}],
 "videos":[{"path":"/dev/video2","name":"USB2.0_CAM1","kind":"capture"}],
 "camera_page":"http://100.120.193.52:8889/cam/"}
```

Config (operator → robot):

```json
{"v":1,"type":"config","link":"tailscale","iface1":"wlp0s20f3","iface2":null,
 "arm":"/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B61033180-if00",
 "video":"/dev/video2"}
```

`link` is `tailscale` or `turn`. `iface2` is a string or null.
Robot replies `{"v":1,"type":"config_ok"}` or
`{"v":1,"type":"error","code":"bad_config","detail":"..."}`.

Start and stop (operator → robot, and the robot's progress back):

```json
{"v":1,"type":"start"}
{"v":1,"type":"stop"}
{"v":1,"type":"status","phase":"starting","detail":"mlink-edge"}
{"v":1,"type":"status","phase":"ready","detail":""}
{"v":1,"type":"status","phase":"stopped","detail":""}
{"v":1,"type":"status","phase":"error","detail":"..."}
```

`phase` is `starting`, `ready`, `stopped`, or `error`.

A new `register` for an ID replaces the previous robot socket.
Password check uses a hash and a constant-time compare. The registry
keeps sessions in memory only.

---

## Layout

```text
packaging/
  ARCHITECTURE.md
  IMPLEMENTATION.md          # this file
  requirements.txt           # P1: websockets, pytest
  pyproject.toml             # P1: pytest testpaths
  registry/                  # P1 server
  robot_app/                 # P2 terminal, P6 supervisor hooks
  operator_app/              # P3 login, P4 config, P7 console
  supervisor/                # P6–P7 command builders
  tests/
  debian/                    # P8
video/so-arm/                # P5 laptop camera launcher
```

Runtime files (gitignored), written by the supervisor, not by hand:

```text
packaging/run/ice-op.yaml
packaging/run/ice-edge.yaml
```

---

## Milestone contracts

### P0 — This plan — STATUS: done

`packaging/ARCHITECTURE.md` and this file. `commit: done`.

---

### P1 — Registry — STATUS: done

**Goal.** Login, pairing, and session relay on the WebSocket server
that already speaks ICE. One process, TCP 8765 on EC2. No second
port. No GUI. No mlink.

**Read:** this file (protocol, locked decisions), `packaging/ARCHITECTURE.md`
section "New model", `turn/signalling/server.py`.

**Implement:**

- `packaging/requirements.txt` — `websockets`, `pytest`.
- `packaging/pyproject.toml` — pytest `testpaths = ["packaging/tests"]`
  or an equivalent that works when pytest is run from `packaging/`.
- `packaging/registry/` — the login session (hash the password,
  one operator per robot, relay inventory / config / start / stop /
  status).
- `turn/signalling/server.py` dispatches on the first message.
  `join` keeps the current ICE room behaviour. `register` and
  `login` enter the registry. Document that in a short
  `packaging/README.md`.
- `packaging/tests/test_registry.py` — in-process server on
  `127.0.0.1` and a free port:
  - robot register, operator login, inventory relayed to the operator
  - wrong password → `auth` and no relay
  - unknown ID → `offline`
  - second operator → `busy`
  - new register replaces the old robot socket
  - an ICE `join` still reaches the existing room logic (one test,
    no STUN)
- Do not bind 8766, 3479, or any port in 50000–50100. Do not open a
  public port from the implementer session.

**Do not:** PyQt, mlink, Docker, `turn/agent` edits, coturn flag
changes, deploy to EC2, log the password.

**Verify:**

```bash
cd /home/muhammadhassan/robots && PYTHONPATH=. python3 -m pytest -q packaging/tests/test_registry.py turn/tests/test_signalling.py
```

**When done:** do not edit the status board and do not commit.
List files, print the pytest command, print manual test steps for
this PC, the robot PC, and EC2, and stop.

P1 landed. Login shares TCP 8765 with ICE `join`. Manual check of
`packaging/docs/P1_usage.md`: robot `register`, operator `login`
with that ID and password, `logged_in`, then inventory relayed.
`commit: done`.

**Session prompt:**

```text
You are the implementer. Branch feature/packaging. Read
packaging/IMPLEMENTATION.md and packaging/ARCHITECTURE.md.
Implement only milestone P1 (login on the existing TCP 8765
signalling server). Follow the protocol and the do-not list in
that milestone. Pytest on localhost. Do not bind 8766, 3479, or
50000-50100. Do not start mlink, Docker, the camera, or the arm.
Do not SSH. Do not commit or push. Do not change the status board.
When P1 works, list files, print the pytest command, then print
manual test steps for this PC, the robot PC, and EC2, and stop.
```

---

### P2 — Robot terminal — STATUS: done

**Goal.** A terminal program that, on launch, generates an ID and
password, prints them, registers with the registry URL, and publishes
inventory when the operator attaches. No PyQt and no display, so it
runs over SSH on the robot PC (including a Jetson). No Start, no
process spawn. The operator app stays a PyQt5 UI. The EC2 registry
is unchanged.

**Read:** P1 protocol, ARCHITECTURE inventory JSON.

**Implement:**

- `packaging/robot_app/` — terminal program plus pure functions:
  - `new_credentials()` → 9-digit ID and 8-character password from
    the alphabet in locked decision 4
  - `inventory_from(sysfs_text, links, serial_dir)` → the inventory
    object. Parse fixtures in tests. Do not require the real `/dev`
    nodes for pytest. When the live program runs, read
    `/sys/class/net`, `/sys/class/video4linux/*/name`, and
    `/dev/serial/by-id`.
  - Default-route NIC via the same data a test can fake.
  - Label `follower` only for by-id containing `5B61033180`,
    `leader` only for `5B3E090040`. Other serial nodes get `serial`.
  - Skip video names that contain `Metadata` (case-insensitive).
  - `camera_page` from `tailscale ip -4` when that command works,
    otherwise `""`.
- Register URL default
  `ws://127.0.0.1:8765`, overridable with `--registry`.
- The terminal prints ID, password, the latest status (`registered`,
  `operator_attached`, or the error code), and the inventory.
  `n` then Enter generates a new password and registers again.
  `q` or Ctrl-C quits.
- On `operator_attached`, send `inventory`.
- `packaging/tests/test_robot_inventory.py` for credentials shape,
  follower default selection helper, metadata skipped, local-only
  NIC flagged `default_route: false`.

**Do not:** PyQt on the robot program. Do not start scripts, Docker,
mlink, the camera, or the arm.
**Do not** import the operator app. **Do not** change the operator
UI plan or the EC2 signalling process.

**Verify:**

```bash
cd /home/muhammadhassan/robots && PYTHONPATH=. python3 -m pytest -q packaging/tests/test_robot_inventory.py packaging/tests/test_registry.py
```

Launch (no display required):

```bash
PYTHONPATH=. python3 -m packaging.robot_app --registry ws://127.0.0.1:8765
```

**When done:** do not edit the status board and do not commit. List files, print the pytest command, print manual test steps for this PC, the robot PC, and EC2, and stop.

**Session prompt:**

```text
You are the implementer. Branch feature/packaging. Read
packaging/IMPLEMENTATION.md and implement only milestone P2
(robot terminal: ID, password, inventory). P1 is already in the
tree. The robot program is a terminal app for SSH, not PyQt. The
operator app stays a UI. Use the protocol as written. Pytest
inventory parsing with fixtures, not live /dev nodes. Do not spawn
mlink, Docker, the camera, or the arm. Do not SSH. Do not commit
or push. When P2 works, do not edit the status board and do not
commit. List files, print the pytest command, print manual test
steps for this PC, the robot PC, and EC2, and stop.
```

P2 landed. `python3 -m packaging.robot_app` prints the ID and
password, registers, and sends inventory when the operator logs in.
No PyQt. Manual check of `packaging/docs/P2_usage.md`: signalling
on EC2, robot process over SSH, operator login with that ID and
password, `logged_in` and inventory. `commit: done`.

---

### P3 — Operator login — STATUS: done

**Goal.** The only GUI in this product. A PyQt5 window with ID and
password fields. Login success shows the robot hostname and a
waiting state. Wrong password shows the error and stays on the
form. No config widgets yet. The robot side stays the P2 terminal.
Do not add a robot window.

**Read:** protocol `login`, `logged_in`, `error`.

**Implement:**

- `packaging/operator_app/` — login page.
- On `logged_in`, remember the socket for P4. Show hostname from
  the robot `register` if the registry includes it in `logged_in`;
  if P1 did not send hostname to the operator, extend the relayed
  `logged_in` with `hostname` in this milestone (small P1-compatible
  addition: extra field, still `v: 1`).
- `packaging/tests/test_operator_login.py` drives the registry with
  a fake robot socket and checks `auth`, `offline`, `busy`, and
  success. GUI widgets can be constructed with `QT_QPA_PLATFORM=offscreen`.
- Default registry `ws://127.0.0.1:8765`, flag `--registry`.

**Do not:** config dropdowns, supervisors, mlink, Docker.

**Verify:**

```bash
cd /home/muhammadhassan/robots && QT_QPA_PLATFORM=offscreen PYTHONPATH=. python3 -m pytest -q packaging/tests
```

**When done:** do not edit the status board and do not commit. List files, print the pytest command, print manual test steps for this PC, the robot PC, and EC2, and stop.

**Session prompt:**

```text
You are the implementer. Branch feature/packaging. Read
packaging/IMPLEMENTATION.md and packaging/ARCHITECTURE.md.
Implement only milestone P3 (operator login window). The robot
program stays the P2 terminal: no PyQt on the robot. Use the
existing registry. Offscreen Qt is enough for tests. Do not add
the config page. Do not spawn mlink, Docker, the camera, or the
arm. Do not SSH. Do not commit or push. Do not change the status
board. When P3 works, list files, print the pytest command, print
manual test steps for this PC, the robot PC, and EC2, and stop.
```

P3 landed. The operator window is PyQt5 login. After the ID and
password from the robot terminal, it shows that robot's hostname.
Manual check of `packaging/docs/P3_usage.md`. The arm stayed stopped.
`commit: done`.

---

### P4 — Config page — STATUS: remaining

**Goal.** After login, the operator window shows Link, Interface 1,
Interface 2, Arm, and Video. Choosing values sends `config`. The
robot terminal prints the choice and replies `config_ok` or
`bad_config`. Nothing is spawned. This milestone does not start
mlink, the camera, or the arm driver.

**Read:** ARCHITECTURE "Suggestion for the config page". Locked
decisions 5–7.

**Implement:**

- Link radios: Tailscale (default) and TURN.
- Interface 1: robot interfaces with `default_route: true`. Default
  the first of those.
- Interface 2: "None" (default) plus other robot interfaces,
  including ones with `default_route: false`, labeled local-only
  when `default_route` is false.
- Arm and Video dropdowns from inventory. Apply the defaults in
  locked decision 7.
- Read-only line for this PC: NIC of the default route, discovered
  locally, not sent as a second bonding choice.
- Help line under Link: "TURN changes gripper control. Video stays
  on the robot camera page."
- Send `config` when the user presses a Review button (not Start;
  Start arrives in P7). Display `config_ok` or `detail`.
- Robot app validates: `link` in {`tailscale`,`turn`}, `iface1` is
  one of its default-route interfaces, `arm` and `video` are in the
  last inventory, `iface2` is null or a known interface name.
  `iface2` not null is still `config_ok` at review time. Refusal of
  a set Interface 2 happens at Start (P6), so the operator can see
  the choice.
- Tests: widget values from a fixture inventory, JSON that hits
  `config_ok`, and one `bad_config` (unknown video path).

**Do not:** spawn processes. Do not open a serial port. Do not start
mlink, Docker, the camera, or the arm. Do not treat Interface 2 as
bonded. Write the manual steps in `packaging/docs/P4_usage.md`.

**Verify:**

```bash
cd /home/muhammadhassan/robots && QT_QPA_PLATFORM=offscreen PYTHONPATH=. python3 -m pytest -q packaging/tests
```

**When done:** do not edit the status board and do not commit. List files, print the pytest command, print manual test steps for this PC, the robot PC, and EC2, and stop.

**Session prompt:**

```text
You are the implementer. Branch feature/packaging. Read
packaging/IMPLEMENTATION.md and packaging/ARCHITECTURE.md section
"Suggestion for the config page". Implement only milestone P4.
Review sends config and does not spawn processes. Interface 2
may be selected at review; do not bond it. Do not start mlink,
Docker, the camera, or the arm. Do not open a serial port. Do not
send g or h. The real gripper is not part of this milestone.
Do not SSH. Do not commit or push. Do not change the status board.
When P4 works, list files, print the pytest command, print manual
test steps for this PC, the robot PC, and EC2, and stop.
Please also write lab test steps in file packaging/docs/P4_usage.md.
```

---

### P5 — Serial port and camera arguments — STATUS: remaining

**Goal.** The existing robot start path accepts a chosen serial
device, and the SO-ARM camera launcher accepts a chosen capture
device. Defaults stay today's paths. No desktop changes required
beyond what P4 already has. No container is started by the
implementer.

**Read:** `teleoperation-prototype/scripts/start_robot_mlink.sh`,
`teleoperation-prototype/compose.robot-mlink.real-arm.yaml`,
`teleoperation-prototype/ros2_ws/src/teleop_demo/teleop_demo/arm_mode.py`,
ARCHITECTURE "What Start launches".

**Implement:**

- `start_robot_mlink.sh` accepts `--serial-port PATH`. Default
  `/dev/ttyACM0`. Export `TELEOP_SERIAL_PORT`. The busy-port check
  and the "missing device" check use that path. `--real-arm` behavior
  is otherwise unchanged. `TELEOP_ARM_PARSE_ONLY=1` still exits 0
  before any device open.
- `compose.robot-mlink.real-arm.yaml` uses
  `${TELEOP_SERIAL_PORT:-/dev/ttyACM0}` for the device mount and
  the environment variable. Gazebo compose still mounts nothing.
- `arm_mode.py` reads `TELEOP_SERIAL_PORT` from the environment when
  set, otherwise `/dev/ttyACM0`. Update the gripper log line to name
  the port in use. Keep the gazebo default.
- Extend `teleoperation-prototype` tests that already cover
  `arm_mode` and the parse-only start script. Assert the default
  port remains `/dev/ttyACM0` when the variable is unset.
- Add `video/so-arm/start.sh`, `stop.sh`, and `gst-publish.sh`.
  Match the Dubai laptop behavior:
  - `DEVICE` defaults to `/dev/video2`
  - unset or default device: sysfs name must contain `USB2.0_CAM1`,
    otherwise exit 2 with the existing refusal (integrated camera
    is `/dev/video0`)
  - `DEVICE` set to another path: the node must exist; if its sysfs
    name contains `Metadata`, exit 2; otherwise allow it and print
    the name
  - encoder is `x264enc` (software), not `nvv4l2h264enc`
  - MediaMTX config listens on TCP 8889, RTP from `127.0.0.1:5004`,
    WebRTC hosts from `tailscale0` plus the host's Tailscale IPv4
    when `tailscale ip -4` works
  - `SO_ARM_CAMERA_PARSE_ONLY=1` prints the resolved device and
    exits 0 without starting MediaMTX
- Do not modify git `video/start.sh` or `video/gst-publish.sh`.

**Do not:** run the script without `PARSE_ONLY` on a machine that
has the arm. Do not start Docker. Do not edit `turn/`.

**Verify:**

```bash
cd /home/muhammadhassan/robots
TELEOP_ARM_PARSE_ONLY=1 ./teleoperation-prototype/scripts/start_robot_mlink.sh --real-arm --serial-port /dev/serial/by-id/usb-1a86_USB_Single_Serial_5B61033180-if00
SO_ARM_CAMERA_PARSE_ONLY=1 DEVICE=/dev/video2 ./video/so-arm/start.sh
PYTHONPATH=. python3 -m pytest -q teleoperation-prototype/ros2_ws/src/teleop_demo/test/test_arm_mode.py packaging/tests
```

**When done:** do not edit the status board and do not commit. List files, print the pytest command, print manual test steps for this PC, the robot PC, and EC2, and stop.

**Session prompt:**

```text
You are the implementer. Branch feature/packaging. Read
packaging/IMPLEMENTATION.md and implement only milestone P5
(serial-port and SO-ARM camera arguments). Leave video/start.sh
unchanged. Defaults remain /dev/ttyACM0 and /dev/video2 with the
USB2.0_CAM1 check. Parse-only flags must not open the arm or
start MediaMTX. Do not SSH. Do not commit or push. When P5
works, do not edit the status board and do not commit. List
files, print the verify commands, print manual test steps for this
PC, the robot PC, and EC2, and stop.
```

---

### P6 — Robot supervisor — STATUS: remaining

**Goal.** Given a `config` that already passed review, build the
robot command list and, when not in dry-run, the robot app will be
able to run it. This session ships the builder, the dry-run path,
and the status messages. The implementer runs dry-run only.

**Read:** ARCHITECTURE "What Start launches", P5 scripts.

**Implement:**

- `packaging/supervisor/robot_commands.py` — pure function
  `robot_plan(config, inventory, repo_root) -> list[command]`.
  - `iface2` not null → plan error `second interface is not used in
    this version; set Interface 2 to None`
  - `link=tailscale` → `start_daemon.sh edge --remote-laptop`
  - `link=turn` → write `packaging/run/ice-edge.yaml` from a caller-
    supplied template string (test passes the template; live code
    reads `turn/config/local_edge.yaml` and replaces `bind_ip` with
    Interface 1's IPv4). Refuse if that IPv4 is missing or starts
    with `100.`. Command is the edge daemon with that yaml, or
    `start_daemon.sh edge --ice` if the daemon grows a
    `--ice-config` in this same milestone. Prefer a new optional
    flag `--ice-config PATH` on `start_daemon.sh` that still selects
    the ICE transport and uses PATH instead of
    `config/lab-edge-ice.yaml`. Without the flag, `--ice` behavior
    stays as it is today.
  - camera: `video/so-arm/start.sh` with `DEVICE` set
  - arm: `start_robot_mlink.sh --real-arm --serial-port <by-id path>`
- `packaging/supervisor/robot_stop.py` — stop order: robot
  `stop_mlink.sh`, then stop the mlink-edge process, then
  `video/so-arm/stop.sh`.
- Robot app on `start`: if `TELEOP_SUPERVISOR_DRY_RUN=1`, send
  `status` `phase=ready` `detail=dry-run` and do not exec. Otherwise
  the exec path exists and is what a later lab runs. Tests call the
  pure function only.
- Tests cover Tailscale plan, TURN plan with a fake yaml template
  and bind address `10.255.254.58`, rejection of `100.120.193.52`,
  rejection of Interface 2, and the stop order.

**Do not:** exec Docker or mlink in tests. Do not SSH. Do not edit
`lab-edge.yaml`. Do not log TURN passwords (copy the yaml without
printing it).

**Verify:**

```bash
cd /home/muhammadhassan/robots && PYTHONPATH=. python3 -m pytest -q packaging/tests
```

**When done:** do not edit the status board and do not commit. List files, print the pytest command, print manual test steps for this PC, the robot PC, and EC2, and stop.

**Session prompt:**

```text
You are the implementer. Branch feature/packaging. Read
packaging/IMPLEMENTATION.md and implement only milestone P6
(robot supervisor command plan and dry-run). Tests call the pure
planner. TELEOP_SUPERVISOR_DRY_RUN=1 must not exec. Refuse
Interface 2 and any 100.x bind address. Do not start the arm.
Do not SSH. Do not commit or push. When P6 works, do not edit the
status board and do not commit. List files, print the pytest
command, print manual test steps for this PC, the robot PC, and
EC2, and stop.
```

---

### P7 — Operator supervisor and console — STATUS: remaining

**Goal.** After the robot reports `ready`, the operator app starts
its half and shows `http://127.0.0.1:8090/` inside the window.
Dry-run does not exec and does not open WebEngine against a live
server.

**Read:** `start_operator_mlink.sh`, `start_daemon.sh`, P6 status
messages.

**Implement:**

- `packaging/supervisor/operator_commands.py`:
  - wait until robot `status.phase == ready` before any operator
    command
  - `link=tailscale` → `start_daemon.sh op --remote-laptop`
  - `link=turn` → `--ice` with optional `--ice-config` pointing at
    a generated `packaging/run/ice-op.yaml` whose `bind_ip` is this
    PC's default-route IPv4 (test injects the address)
  - then `start_operator_mlink.sh --remote-laptop` so
    `TELEOP_GRIPPER_ONLY=1` stays on, with `REMOTE_LAPTOP_CAM` set
    from inventory `camera_page` (empty camera page → plan error
    on the status line, do not invent a URL)
  - console URL remains `http://127.0.0.1:8090/` plus the `cam`
    query the script already builds
- Operator window: Start sends `start`, shows `status.detail` as it
  arrives, and on `ready` (and not dry-run) loads the console URL
  in `QWebEngineView`. Stop sends `stop` and then runs the local
  operator stop (compose down, mlink-op process). Closing the window
  does the same local operator stop.
- `TELEOP_SUPERVISOR_DRY_RUN=1` skips exec and skips loading
  WebEngine. It still sends `start` and displays the robot's
  dry-run status.
- Tests: plan contents for both links, no operator command when the
  robot phase is `error`, camera page required, close-handler calls
  the local stop function (stubbed).

**Do not:** run the real compose in tests. Do not SSH. Do not point
the camera at coturn. Do not rewrite `web/operate.js`.

**Verify:**

```bash
cd /home/muhammadhassan/robots && QT_QPA_PLATFORM=offscreen PYTHONPATH=. python3 -m pytest -q packaging/tests
```

**Lab, for the user after this milestone, not for the implementer:**

1. Signalling already listening on EC2 TCP 8765. Desktop apps use `ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765`.
2. Robot terminal over SSH, then the operator app. Log in with the ID and password printed by the robot terminal.
3. Link Tailscale, Interface 2 None, follower serial, `USB2.0_CAM1`. Start.
4. HUD CONNECTED, one tap `g` or `h`, then close the operator window and confirm the gripper torque goes off.
5. Repeat with Link TURN only when coturn and ICE signalling are already up.

**When done:** do not edit the status board and do not commit.
List files, print the pytest command, print the lab steps, and stop.

**Session prompt:**

```text
You are the implementer. Branch feature/packaging. Read
packaging/IMPLEMENTATION.md and implement only milestone P7
(operator supervisor and in-app console). Dry-run and pytest
only. Do not start Docker or the arm. Do not SSH. Do not commit
or push. When P7 works, do not edit the status board and do not
commit. List files, print the pytest command and the lab steps
from the milestone, and stop.
```

---

### P8 — Debian packages — STATUS: remaining

**Goal.** Two installable amd64 `.deb` files. The operator package
adds an application-menu launcher. The robot package installs a
terminal command and no GUI. Installing either does not start the
arm.

**Read:** ARCHITECTURE "Installable packages".

**Implement:**

- `packaging/debian/build.sh` builds
  `packaging/dist/teleop-robot_<version>_amd64.deb` and
  `packaging/dist/teleop-operator_<version>_amd64.deb`.
  `packaging/dist/` is gitignored.
- Payload is the `packaging/` Python tree, `mlink-transport/` (no
  `__pycache__`), `turn/` without gitignored yaml and without
  `scripts/coturn.env`, `video/so-arm/`, and the teleoperation
  start scripts the supervisor calls. Install prefix `/opt/teleop`.
- Operator `.desktop` runs the operator app. The robot package has
  no `.desktop` file. Both use `/usr/bin/python3` with
  `PYTHONPATH=/opt/teleop`. The robot command is
  `python3 -m packaging.robot_app`.
- Depends: `python3`. Operator also depends on `python3-pyqt5` and
  `python3-pyqt5.qtwebengine`. The robot package does not depend
  on PyQt.
  `websockets` is vendored or listed as a dependency that exists on
  both Ubuntu 22.04 and 24.04; if the distro package name differs,
  vendor the small library inside the package rather than using pip
  at install time.
- If `video/bin/mediamtx` exists at build time, copy it into the
  robot package at `/opt/teleop/video/so-arm/bin/mediamtx`. Do not
  `git add` the binary.
- `postinst` does not start processes. A `--check` flag on each app
  prints whether `docker` and image `ros2-teleop-poc:humble` are
  present, and exits 0 even when they are absent (the Start button
  surfaces that later).
- `build.sh` runs `dpkg-deb -I` and `dpkg-deb -c` and fails if
  either package is empty or if a secret filename (`local_op.yaml`,
  `local_edge.yaml`, `coturn.env`) is inside.

**Do not:** `apt install` on the robot over SSH. Do not include the
ROS image. Do not push.

**Verify:**

```bash
cd /home/muhammadhassan/robots && ./packaging/debian/build.sh
dpkg-deb -I packaging/dist/teleop-operator_*_amd64.deb
dpkg-deb -I packaging/dist/teleop-robot_*_amd64.deb
```

**When done:** do not edit the status board and do not commit. List files, print the pytest command, print manual test steps for this PC, the robot PC, and EC2, and stop.

**Session prompt:**

```text
You are the implementer. Branch feature/packaging. Read
packaging/IMPLEMENTATION.md and implement only milestone P8
(two .deb packages). build.sh must succeed locally. Do not apt
install on the robot, do not SSH, do not include secret yaml or
the ROS image, and do not git add mediamtx. Do not commit or
push. When P8 works, do not edit the status board and do not
commit. List files, print the dpkg-deb commands, and stop.
```

---

## Lab safety (every milestone)

The person at the robot watches the arm. First motion is one tap of
`g` or `h` with the gripper clear, after the HUD says CONNECTED.
Closing the operator app is the soft stop. Packaging sessions do
not run that lab; the user does, after P7.
