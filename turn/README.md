# ICE / STUN / TURN (direct subpaths)

NAT traversal for two endpoints: **ICE hole punch** when the NATs
allow it, **coturn on EC2** when they do not. Python `aioice` on both
nodes. We do not write a TURN server.

This tree is **not** mlink and **not** teleop until a later plan wires
a nominated path into `mlink-transport/`.

**Start here:** [`IMPLEMENTATION.md`](IMPLEMENTATION.md) — status
board, locked decisions, milestone contracts, session prompts.

Hello-world (milestones T1–T5): two NAT’d nodes, one NIC each, STUN,
signalling, punch or TURN, echo `hello`.
