# Robot password

The robot terminal prints a 9-digit ID. The person at that SSH session
types a password with no echo, then types it again. The terminal
registers that password with the signalling server. The server keeps a
hash in memory only. The operator window is unchanged: type the ID from
the robot terminal and the password that was set there.

This check does not start teleop. Leave the arm, camera, and mlink
stopped. Do not press Start. Do not open a serial port. Do not send
`g` or `h`. Coturn stays up.

The password is typed at the prompt. It must not appear in the terminal
after that, on the status line, in a file, or in a log. Do not write it
into git or into this file. Older lab notes that show a printed
password are out of date for this check.

## 1. This PC — pytest

```bash
cd /home/muhammadhassan/robots
QT_QPA_PLATFORM=offscreen PYTHONPATH=. python3 -m pytest -q packaging/tests
```

Pass: the packaging tests pass. That run does not start mlink, Docker,
the camera, or the arm, and it does not open a serial port.

## 2. This PC — local terminal

`websockets` must import. If it does not:

```bash
/usr/bin/python3 -m pip install --user websockets
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

Expect:

```text
Robot  registry ws://127.0.0.1:8765
ID <9 digits>
New password:
```

Type a password. The characters stay hidden. Press Enter.

```text
Retype new password:
```

Press Enter with nothing typed. Expect `No password supplied.` and both
prompts again. Type two different passwords. Expect
`Sorry, passwords do not match.` and both prompts again. Type the same
password twice. Expect `Password updated.` and then:

```text
ID <9 digits>  status waiting
```

The inventory for this PC follows, then:

```text
Type n and press Enter to set a new password. The ID stays the same. Type q or Ctrl-C to quit.
ID <9 digits>  status registered
```

Each status line is the ID and the status. The password you typed is
not on the screen. Leave Terminal B open.

Terminal C:

```bash
cd /home/muhammadhassan/robots
PYTHONPATH=. python3 -m packaging.operator_app --registry ws://127.0.0.1:8765
```

Type the ID from Terminal B and the password you set. Pass: the window
shows this PC's hostname and `Waiting for the robot.` Terminal B prints
`ID <9 digits>  status operator_attached`. A wrong password shows
`Wrong password.` and the form stays.

In Terminal B, type `n` and press Enter. The same two prompts appear.
Set a different password the same way. The ID on the next status line
is the same 9 digits. The new password is not printed. In the operator
window, the previous password shows `Wrong password.` The new password
logs in.

Type `q` in Terminal B, close the operator window, and Ctrl-C Terminal A.

## 3. EC2 — leave signalling up

This change does not modify the signalling server. The process already
stores a salted hash in memory and does not write the password.

On EC2:

```bash
ss -ltnp | grep 8765
```

Expect the existing `signalling.server` process. Leave it. Leave coturn
as it is. Do not rsync. Do not restart signalling while it is up: a
restart drops sessions that are using that port. Do not search the
process output for a password.

If nothing is listening, start the usual process and leave it open:

```bash
cd ~/turn && . .venv/bin/activate
python3 -m signalling.server --bind 0.0.0.0 --port 8765
```

Expect `signalling ws://0.0.0.0:8765`.

## 4. Robot PC — terminal over SSH

From this PC:

```bash
cd /home/muhammadhassan/robots
rsync -av --exclude tests --exclude __pycache__ --exclude run packaging/ \
  muhammad-osama@100.120.193.52:~/teleops_hassan/packaging/
```

On the robot PC, over SSH. Use a real terminal so echo can be turned
off. Use `/usr/bin/python3`, not Miniforge. If that Python cannot import
`websockets`:

```bash
/usr/bin/python3 -m pip install --user websockets
```

```bash
cd ~/teleops_hassan
PYTHONPATH=. /usr/bin/python3 -m packaging.robot_app \
  --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

Expect `ID <9 digits>`, then `New password:` and `Retype new password:`.
Typing is hidden. An empty entry or a mismatch asks again. After the
two entries match, expect `Password updated.`, then
`ID <9 digits>  status waiting`, then
`ID <9 digits>  status registered`. The password is not on the screen.
Leave this terminal open. Do not start the operator window here. Do not
press Start.

Type `n` and press Enter. Set a replacement the same way. The ID stays
the same. The replacement is not printed.

## 5. This PC — operator window

```bash
cd /home/muhammadhassan/robots
PYTHONPATH=. python3 -m packaging.operator_app \
  --registry ws://ec2-3-227-234-95.compute-1.amazonaws.com:8765
```

Type the ID from the robot terminal and the password set in step 4.
Pass: the window shows `AUTOOS-DEV-MUHAMMADOSAMA` and
`Waiting for the robot.` The robot terminal prints
`ID <9 digits>  status operator_attached`.

The previous password, if you replaced it with `n`, shows
`Wrong password.` and the form stays. Close the window when the check
is done. Type `q` in the robot terminal. Leave the arm stopped.
