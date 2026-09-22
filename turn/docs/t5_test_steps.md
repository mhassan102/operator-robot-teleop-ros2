# T5 test steps

Two NAT’d PCs, coturn and signalling on EC2. No mlink. TURN is UDP only.

`ice_policy` is one line in each `config/local.yaml`:

- `all` — gather host, srflx, and relay. The pair that answers is nominated. A direct punch can win. A relay pair can also win, which is what the 2026-09-22 run did.
- `relay` — keep only the coturn relay candidate. Host and srflx are dropped, so the nominated pair is relay even when a direct punch would work. This is Lab A.

The word in the yaml is `relay`, not replay.

Addresses for this lab: operator wifi `192.168.222.43` (controlling), laptop wifi `10.255.254.58` (controlled), coturn `3.227.234.95` UDP `3478`, relay ports `50000-50100`. TURN user `labturn`. Password is already in both gitignored `config/local.yaml` files. Same room `hello-world`.

## 1. Coturn

On EC2:

```bash
sudo docker ps --filter name=coturn
```

If the name `coturn` is listed, `sudo docker start coturn` is enough. If `docker ps -a` is empty:

```bash
bash ~/coturn/run-coturn.sh
```

Expect `coturn` with status `Up`.

## 2. UDP check

From the operator and from the laptop:

```bash
cd ~/robots/turn    # laptop: ~/teleops_hassan/turn
python3 scripts/stun_probe.py 3.227.234.95 3478
```

Expect:

```text
reply_from=3.227.234.95:3478 nbytes=100 type=0x0101
```

## 3. Signalling

On EC2, leave this running. It only exchanges ICE username, password, and candidates. It does not carry `hello`.

```bash
cd ~/turn && . .venv/bin/activate
python3 -m signalling.server --bind 0.0.0.0 --port 8765
```

Expect:

```text
signalling ws://0.0.0.0:8765
```

The server stays quiet after that. Signalling succeeded when both agents print a `path=` line. That only happens after each side has received the other’s candidates.

## 4. Agents

Start both within about 60 seconds.

Operator:

```bash
cd ~/robots/turn
python3 -m agent run --config config/local.yaml
```

Laptop:

```bash
cd ~/teleops_hassan/turn
. .venv/bin/activate
python3 -m agent run --config config/local.yaml
```

### `ice_policy: all` (the run already taken)

Success is `hello` / `hello-ack` on both sides. `path=turn` means at least one end of the nominated pair is a relay address, so the bytes go through coturn. `path=direct` would mean both ends are host or srflx and EC2 is not on the data path.

Observed:

```text
# laptop
ice_policy=all
path=turn local=relay:3.227.234.95:50009 remote=srflx:43.246.227.66:51865
recv hello
hello-ack

# operator
ice_policy=all
path=turn local=host:192.168.222.43:51865 remote=relay:3.227.234.95:50009
sent hello
hello-ack
```

That pair is one path. The operator sends from its own wifi socket to the laptop’s relay `3.227.234.95:50009`. Coturn copies those packets to the laptop. The laptop sends back through that same allocation toward the operator’s public mapping `43.246.227.66:51865`. The operator’s own relay `3.227.234.95:50018` was gathered and not used. This is TURN, not a direct punch.

Both then print `holding path (ICE consent keepalive); Ctrl-C to stop`.

### `ice_policy: relay` (Lab A)

Set `ice_policy: relay` on both yaml files, then run the same two commands. Expect only a `relay` gather line on each side, then:

```text
path=turn local=relay:3.227.234.95:<port> remote=relay:3.227.234.95:<port>
```

and the same `hello` / `hello-ack`. Both ports are in `50000-50100`.

## 5. Stop

Ctrl-C both agents and the signalling process. On EC2: `sudo docker stop coturn`.
