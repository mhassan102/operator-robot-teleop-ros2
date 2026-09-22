#!/usr/bin/env python3
"""Send one STUN Binding request. Exit 0 on success response 0x0101.

Usage: python3 scripts/stun_probe.py HOST PORT
nc -u is not a STUN check. TURN-TCP is not this probe.
"""

from __future__ import annotations

import os
import socket
import struct
import sys

BINDING_REQUEST = 0x0001
BINDING_SUCCESS = 0x0101
COOKIE = 0x2112A442


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: stun_probe.py HOST PORT", file=sys.stderr)
        return 2
    host, port_s = argv[1], argv[2]
    port = int(port_s)
    req = struct.pack("!HHI12s", BINDING_REQUEST, 0, COOKIE, os.urandom(12))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3)
    try:
        sock.sendto(req, (host, port))
        data, addr = sock.recvfrom(2048)
    except socket.timeout:
        print(f"timeout waiting for {host}:{port}", file=sys.stderr)
        return 1
    finally:
        sock.close()
    if len(data) < 2:
        print(f"short reply from {addr[0]}:{addr[1]}", file=sys.stderr)
        return 1
    typ = int.from_bytes(data[:2], "big")
    print(
        f"reply_from={addr[0]}:{addr[1]} nbytes={len(data)} type=0x{typ:04x}"
    )
    if typ != BINDING_SUCCESS:
        print("not a STUN Binding success", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
