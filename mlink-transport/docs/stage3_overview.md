# Stage 3 overview (two machines, real eth + wifi)

Same `mlink-op` / `mlink-edge` / `mlink-ping` as Stage 2. Sockets bind
real NICs. When YAML `ifname` is set, `UdpSocketFactory` applies
`SO_BINDTODEVICE` so copies cannot leak onto another interface (and
cannot use `tailscale0`).

Linux default route stays on **Wi-Fi**. Ethernet is a point-to-point
`/24` on the USB dongle. Tailscale is SSH only.

Live addresses (must match `ip -br addr`; edit the YAML if DHCP moved):

```text
OPERATOR (pure-dev-muhammadhassan)                 ORIN (nvidia-3)
wlo1  192.168.222.139  -- Guest Wi-Fi --  wlP1p1s0  192.168.223.44
USB-eth enx00e04c681cc3 192.168.108.1 --cable-- eno1  192.168.108.120
app face 127.0.0.1:5501/5502                    --reflect (5503/5504 unused)
UDP 46000 on each path bind IP
```

```text
mlink-ping                 mlink-op                         mlink-edge [--reflect]
    │                      (this PC)                        (Orin)
    │  127.0.0.1:5501           │                                  │
    │──────────────────────────►│                                  │
    │                           │  copy seq=N path_id=0 eth        │
    │                           │  192.168.108.1:46000             │
    │                           │  → 192.168.108.120:46000         │
    │                           │─────────────────────────────────►│
    │                           │  copy seq=N path_id=1 wifi       │
    │                           │  192.168.222.139:46000           │
    │                           │  → 192.168.223.44:46000          │
    │                           │─────────────────────────────────►│
    │                           │                     first good wins
    │                           │                     later copy dropped
    │                           │                     session.send reflect
    │                           │◄─────────────────────────────────│
    │  127.0.0.1:5502           │     copies on both paths again   │
    │◄──────────────────────────│                                  │
```

Cable pull: unplug USB-Ethernet (or `nmcli device disconnect
enx00e04c681cc3`). Wi-Fi copies already carry the stream; mlink marks
`eth` down after 300 ms of silence and probes at 1 Hz. SSH over Guest
Wi-Fi (`ssh nvidia@192.168.223.44`) and Tailscale must survive.

`ifname` unset → same as Stage 2 (no `SO_BINDTODEVICE`).
