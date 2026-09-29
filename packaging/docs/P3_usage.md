# P3 lab check

Open the operator window, type the ID and password printed by the
robot terminal, and confirm the robot hostname. Leave the arm,
camera, and mlink stopped. Coturn stays up. The robot program stays
the terminal. Do not install PyQt on the robot PC.

The password is printed in the robot terminal for this check. Do not
write it into git, a log, or this file.

## 1. This PC — local window, then copy

`websockets` must import. If it does not:

```bash
/usr/bin/python3 -m pip install --user websockets
```

The window needs PyQt5 from apt:

```bash
sudo apt install python3-pyqt5
```

Terminal A:

```bash
cd /home/muhammadhassan/robots/turn
PYTHONPATH=/home/muhammadhassan/robots python3 -m signalling.server --bind 127.0.0.1 --port 8765
```

Expect `signalling ws://127.0.0.1:8765`. Leave it open. Terminal B:

```bash
cd /home/muhammadhassan/robots
PYTHONPATH=. python3 -m packaging.robot_app --registry ws://127.0.0.1:8765
```

Expect `ID <9 digits>  Password <8 characters>  status registered`.
Terminal C:

```bash
cd /home/muhammadhassan/robots
PYTHONPATH=. python3 -m packaging.operator_app --registry ws://127.0.0.1:8765
```

Type that ID and password. Pass: the window shows this PC's hostname
and `Waiting for the robot.` Terminal B prints `status operator_attached`.

A wrong password shows `Wrong password.` and the form stays. A second
window with the same ID shows `Another operator is already connected.`
Close the first window and the second login succeeds. An unknown
9-digit ID shows `That robot is offline.`

Type `q` in the robot terminal, and Ctrl-C Terminal A, before the
robot-PC check.

Copy onto EC2 and the robot PC:

```bash
cd /home/muhammadhassan/robots
rsync -av turn/signalling/server.py ec2-user@3.227.234.95:~/turn/signalling/server.py
rsync -av --exclude tests --exclude __pycache__ packaging/ ec2-user@3.227.234.95:~/turn/packaging/
rsync -av --exclude tests --exclude __pycache__ packaging/ muhammad-osama@100.120.193.52:~/teleops_hassan/packaging/
```

## 2. EC2 — restart signalling

Stop the process `ss -ltnp | grep 8765` shows, then:

```bash
cd ~/turn && . .venv/bin/activate
python3 -m signalling.server --bind 0.0.0.0 --port 8765
```

Expect `signalling ws://0.0.0.0:8765`. Leave that terminal open.
Coturn stays as it is. This restart picks up the login reply that
includes the robot hostname.

## 3. Robot PC — terminal over SSH, and leave it open

```bash
cd ~/teleops_hassan
PYTHONPATH=. /usr/bin/python3 -m packaging.robot_app \
  --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

Use `/usr/bin/python3` so the Miniforge `(base)` prompt is not the
interpreter. If that Python cannot import `websockets`:

```bash
/usr/bin/python3 -m pip install --user websockets
```

Expect `status registered`. Read the ID and password from that
terminal. Leave it open. Do not start the operator window here.

## 4. This PC — operator window

```bash
cd /home/muhammadhassan/robots
PYTHONPATH=. python3 -m packaging.operator_app \
  --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

Pass: the window shows `AUTOOS-DEV-MUHAMMADOSAMA` and
`Waiting for the robot.` The robot terminal prints
`status operator_attached`.

A wrong password shows `Wrong password.` and the form stays. A second
window while the first is still open shows
`Another operator is already connected.` Close the first window, then
the second login succeeds. Close the window before quitting the robot
terminal.
