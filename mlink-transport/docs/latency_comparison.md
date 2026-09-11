# Path latency comparison (RTT)

Round-trip times. Lab Ethernet / Wi-Fi / Tailscale are measured. WAN
rows are typical estimates (~2× one-way). Localhost is a process hop,
not a WAN path.

| Path | Typical RTT | Range | Source |
| --- | --- | --- | --- |
| Localhost process hop | 0.05–0.3 ms | up to ~1 ms | estimate |
| Ethernet cable (LAN) | ~1.3 ms | ~1–2 ms | lab mlink |
| Guest Wi-Fi (LAN) | 10–50 ms | ~8–80 ms | lab mlink |
| 5G dongle (WAN) | 80–160 ms | ~50–400 ms if LTE/congested | estimate |
| Port-forward (WAN) | 30–80 ms | ~10–160 ms by distance | estimate |
| VPS / relay (WAN) | 40–100 ms if VPS is nearby | 300+ ms if VPS is far | estimate (always a detour vs PF) |
| Tailscale (`100.101.94.5`) | ~12 ms or ~120–170 ms | 12–272 ms this ping, mean ~120 ms | ping |

## Tailscale ping (this lab)

`PING nvidia-3.tail40aa1c.ts.net (100.101.94.5)`:

| icmp_seq | RTT |
| --- | --- |
| 1 | 272 ms |
| 2 | 12.6 ms |
| 3 | 116 ms |
| 4 | 12.2 ms |
| 5 | 138 ms |
| 6 | 165 ms |

Two samples look **direct** (~12 ms, similar to lab Wi-Fi). The others
look like a **relay** (100–270 ms). Mean of these six is ~120 ms.

## Notes

- VPS is not the same 15–40 ms as port-forward. That 15–40 ms was extra
  **one-way** vs a direct path. Nearby VPS ≈ PF + extra hop; a distant
  VPS can be worse than 5G.
- Tailscale is SSH/management only. It is not a bonded mlink data path.
