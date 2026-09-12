#!/usr/bin/env python3
"""Stdlib WebSocket client for test_console_session.sh.

Connects to ws://127.0.0.1:8090/ws/session, then reads line commands on stdin:

  DOWN <key>
  UP <key>
  STOP
  CLOSE

Prints SESSION <id> after the server greeting, and one OK line per command.
"""

from __future__ import annotations

import base64
import json
import os
import select
import socket
import struct
import sys
import threading

HOST = os.environ.get("TELEOP_CONSOLE_HOST", "127.0.0.1")
PORT = int(os.environ.get("TELEOP_CONSOLE_PORT", "8090"))
PATH = "/ws/session"
WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_TEXT = 0x1
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class ClientClosed(Exception):
    pass


class WsClient:
    def __init__(self, sock: socket.socket, leftover: bytes) -> None:
        self.sock = sock
        self._buf = bytearray(leftover)
        self._lock = threading.Lock()
        self.closed = False

    def send_json(self, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self._send_frame(OP_TEXT, raw)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._send_frame(OP_CLOSE, b"")
        except OSError:
            pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def read_json(self) -> dict | None:
        while not self.closed:
            opcode, payload = self._read_frame()
            if opcode == OP_CLOSE:
                self.close()
                return None
            if opcode == OP_PING:
                self._send_frame(OP_PONG, payload)
                continue
            if opcode != OP_TEXT:
                continue
            data = json.loads(payload.decode("utf-8"))
            if isinstance(data, dict):
                return data
        return None

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", 0x80 | opcode, 0x80 | length)
        elif length < 65536:
            header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, length)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        with self._lock:
            self.sock.sendall(header + mask + masked)

    def _read_at_least(self, count: int) -> None:
        while len(self._buf) < count:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ClientClosed
            self._buf.extend(chunk)

    def _take(self, count: int) -> bytes:
        self._read_at_least(count)
        data = bytes(self._buf[:count])
        del self._buf[:count]
        return data

    def _read_frame(self) -> tuple[int, bytes]:
        header = self._take(2)
        b0, b1 = header[0], header[1]
        opcode = b0 & 0x0F
        masked = (b1 & 0x80) != 0
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._take(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._take(8))[0]
        mask = self._take(4) if masked else b""
        payload = self._take(length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return opcode, payload


def handshake() -> WsClient:
    sock = socket.create_connection((HOST, PORT), timeout=5)
    sock.settimeout(None)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {PATH} HTTP/1.1\r\n"
        f"Host: {HOST}:{PORT}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise SystemExit("handshake closed")
        buf += chunk
    header, leftover = buf.split(b"\r\n\r\n", 1)
    status = header.split(b"\r\n", 1)[0]
    if b" 101 " not in status:
        raise SystemExit(f"handshake failed: {status!r}")
    return WsClient(sock, leftover)


def _emit(line: str) -> None:
    print(line, flush=True)


def main() -> None:
    client = handshake()
    greeting = client.read_json()
    session_id = "unknown"
    if greeting and greeting.get("type") == "session":
        session_id = str(greeting.get("session_id") or "unknown")
    _emit(f"SESSION {session_id}")

    try:
        while True:
            ready, _unused, _unused2 = select.select([sys.stdin, client.sock], [], [])
            if client.sock in ready:
                try:
                    message = client.read_json()
                except (ClientClosed, OSError, json.JSONDecodeError, struct.error):
                    _emit("CLOSED")
                    return
                if message is None:
                    _emit("CLOSED")
                    return
            if sys.stdin not in ready:
                continue
            line = sys.stdin.readline()
            if line == "":
                client.close()
                _emit("CLOSED")
                return
            parts = line.strip().split()
            if not parts:
                continue
            cmd = parts[0].upper()
            if cmd == "CLOSE":
                client.close()
                _emit("CLOSED")
                return
            if cmd == "STOP":
                client.send_json({"type": "key", "key": " ", "down": True})
                _emit("OK STOP")
                continue
            if cmd in ("DOWN", "UP") and len(parts) >= 2:
                key = parts[1]
                if key in ("space", "SPACE"):
                    key = " "
                client.send_json(
                    {"type": "key", "key": key, "down": cmd == "DOWN"}
                )
                _emit(f"OK {cmd} {key}")
                continue
            _emit(f"ERR unknown {line.strip()!r}")
    except (ClientClosed, OSError, BrokenPipeError):
        _emit("CLOSED")
    finally:
        client.close()


if __name__ == "__main__":
    main()
