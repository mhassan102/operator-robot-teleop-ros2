# Safety

Software safety for the teleop POC. **Not** IEC 61508 / ISO 10218 /
SIL. Hardware E-stop, STO, and a safety PLC are out of this prototype.

Product roadmap: [`../IMPLEMENTATION.md`](../IMPLEMENTATION.md)
(Features F4, F11, F12, F15).

| File | What |
| ---- | ---- |
| [`robotics_safety_functions_25.txt`](robotics_safety_functions_25.txt) | Consultant list (titled 25; 23 visible boxes) |
| [`coverage-analysis.md`](coverage-analysis.md) | How the current POC maps onto those functions |
| [`zone1-implementation-plan.md`](zone1-implementation-plan.md) | Zone 1 (stop / restart) implementation plan |

## Status

| Layer | Status |
| ----- | ------ |
| v0 gateway: clamp, 500 ms watchdog, `/cmd_vel_safe` | **done** (jog path only) |
| Safety-A (named-pose gate, bridge latch, restart hold) | **on hold** — planned, not started |
| Safety-B (WAN `heartbeat_only`, software E-stop, Reset) | remaining, after Safety-A |
| Video-freshness as a stop input | remaining, after the camera is in the console |
| Certified safety / Zone 2–4 hardware | out of prototype |

Integration of Safety-A is on hold by product decision. It is still
**required before WAN / real arm**. Do not start Safety-A in a session
that is not explicitly unblocking F11.
