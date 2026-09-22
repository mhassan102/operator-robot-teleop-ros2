# coturn on EC2 (T5)

We run **coturn**. We do not write a TURN server. No Twilio. TURN-TCP
and port 443 are out of T5: clients use UDP only.

Lab host: `ec2-user@ec2-3-227-234-95.compute-1.amazonaws.com`
(public `3.227.234.95`, private `172.31.73.32`, security group
`sg-3c8c470f`). Signalling stays on TCP `8765`. Coturn does not relay
`hello`; aioice does, after Allocate.

## Process

Docker image `coturn/coturn:4.6.2`, **host network** (relay ports must
be the host's ports). Copy `coturn.env.example` to `coturn.env`
(gitignored) and set `TURN_PASSWORD`.

```bash
# on the EC2 host, from turn/scripts, after docker is installed
./run-coturn.sh
docker logs coturn --tail 40
```

Equivalent compose file: `docker-compose.yml` (`docker compose
--env-file coturn.env -f docker-compose.yml up -d`). Amazon Linux
2023 may not have the compose plugin; `run-coturn.sh` is enough.

Flags: listen UDP `3478`, `--lt-cred-mech`, realm `teleop.lab`,
`--user`, `--external-ip=<public>/<private>`, `--relay-ip=<private>`,
relay ports `50000-50100`, `--no-tcp`, `--no-tls`, `--no-dtls`,
`--no-cli`. `--external-ip=PUBLIC/PRIVATE` is required on EC2 so
XOR-RELAYED-ADDRESS is `3.227.234.95`, not `172.31.73.32`.

## Security group

Inbound on `sg-3c8c470f`, source `0.0.0.0/0` (lab NATs change):

| proto | ports | why |
| ----- | ----- | --- |
| UDP | 3478 | STUN Binding and TURN Allocate |
| UDP | 50000-50100 | coturn relay ports (`--min-port` / `--max-port`) |
| TCP | 8765 | signalling (already used; not TURN) |

No TURN-TCP, no 443, no 5349. This EC2 instance has no IAM role, and
the operator PC has no AWS CLI credentials. Open the group from a
machine that can call `ec2:AuthorizeSecurityGroupIngress`.

```bash
aws ec2 authorize-security-group-ingress --group-id sg-3c8c470f \
  --ip-permissions \
  'IpProtocol=udp,FromPort=3478,ToPort=3478,IpRanges=[{CidrIp=0.0.0.0/0}]' \
  'IpProtocol=udp,FromPort=50000,ToPort=50100,IpRanges=[{CidrIp=0.0.0.0/0}]'
```

OS firewall on this AL2023 host is not filtering (no iptables/nft
rules installed). A timeout from outside with coturn listening on
`0.0.0.0:3478` is the security group or an upstream UDP filter.

## Lab 0 — UDP probe

STUN Binding (empty attributes). A reply is success response type
`0x0101`. `nc -u` alone is not a STUN check. Run from the laptop
(`10.255.254.58`) and from the operator. If the operator gets a reply
and the laptop does not, UDP to this EIP is filtered on the laptop
wifi and UDP TURN cannot work there. Stop. Do not add TURN-TCP.

```bash
python3 scripts/stun_probe.py 3.227.234.95 3478
# reply_from=3.227.234.95:3478 ... type=0x0101
```

On the EC2 host the same probe to `127.0.0.1:3478` must succeed before
blaming the laptop. `ss -ulnp | grep 3478` should show `turnserver`.

Measured 2026-09-22: Binding success from the operator and from the
laptop (`10.255.254.58`), and TURN Allocate returned
`3.227.234.95:<relay-port>`. A probe to that relay port did not show
up in tcpdump on the instance. UDP `3478` is open. UDP `49152-49200`
is not. UDP `50000-50100` already arrives from both lab NATs, so
coturn's relay range is that range. No TURN-TCP.

## Agents

`config/local.yaml` (gitignored) on each node: `turn_server:
3.227.234.95`, `turn_port: 3478`, the same user and password as
`coturn.env`, `stun_server: 3.227.234.95`, `stun_port: 3478`.
`ice_policy: relay` forces a relay pair (Lab A). `all` gathers
host/srflx/relay; aioice nominates the first pair that answers, so
block host/srflx to prove fallback (Lab B). Tailscale `100.x` is not
a TURN server.

Signalling was not left running. Start it before Lab A or B:

```bash
cd ~/turn && . .venv/bin/activate
python3 -m signalling.server --bind 0.0.0.0 --port 8765
```
