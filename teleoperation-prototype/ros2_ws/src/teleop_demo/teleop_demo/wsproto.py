"""Minimal RFC 6455 WebSocket for the operator console (stdlib only)."""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
from typing import Optional

WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_TEXT = 0x1
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA
MAX_PAYLOAD = 65536


class WebSocketClosed(Exception):
    """Peer closed the socket or sent a close frame."""


def handshake_accept(key: str) -> str:
    digest = hashlib.sha1((key + WS_MAGIC).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def is_websocket_upgrade(headers) -> bool:
    upgrade = headers.get("Upgrade", "").lower().strip()
    connection = headers.get("Connection", "").lower()
    version = headers.get("Sec-WebSocket-Version", "").strip()
    key = headers.get("Sec-WebSocket-Key", "").strip()
    return (
        upgrade == "websocket"
        and "upgrade" in connection
        and version == "13"
        and bool(key)
    )


class WsConnection:
    """One accepted server-side WebSocket. Thread-safe send, blocking read."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self._send_lock = threading.Lock()
        self._closed = threading.Event()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self._send_frame(OP_CLOSE, b"")
        except OSError:
            pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def send_json(self, payload: dict) -> None:
        self._send_frame(OP_TEXT, json.dumps(payload).encode("utf-8"))

    def read_json(self) -> Optional[dict]:
        """Return the next text JSON object, or None on close."""
        while not self._closed.is_set():
            try:
                opcode, payload = self._read_frame()
            except (WebSocketClosed, OSError, struct.error):
                self._closed.set()
                return None
            if opcode == OP_CLOSE:
                self.close()
                return None
            if opcode == OP_PING:
                try:
                    self._send_frame(OP_PONG, payload)
                except OSError:
                    self._closed.set()
                    return None
                continue
            if opcode == OP_PONG:
                continue
            if opcode != OP_TEXT:
                continue
            try:
                data = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                return data
        return None

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", 0x80 | opcode, length)
        elif length < 65536:
            header = struct.pack("!BBH", 0x80 | opcode, 126, length)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 127, length)
        with self._send_lock:
            if self._closed.is_set() and opcode != OP_CLOSE:
                raise OSError("websocket closed")
            self.sock.sendall(header + payload)

    def _recv_exact(self, count: int) -> bytes:
        buf = bytearray()
        while len(buf) < count:
            chunk = self.sock.recv(count - len(buf))
            if not chunk:
                raise WebSocketClosed
            buf.extend(chunk)
        return bytes(buf)

    def _read_frame(self) -> tuple[int, bytes]:
        header = self._recv_exact(2)
        b0, b1 = header[0], header[1]
        fin = (b0 & 0x80) != 0
        opcode = b0 & 0x0F
        masked = (b1 & 0x80) != 0
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        if not fin or length > MAX_PAYLOAD or not masked:
            raise WebSocketClosed
        mask = self._recv_exact(4)
        payload = self._recv_exact(length) if length else b""
        payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return opcode, payload
