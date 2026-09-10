"""mlink-ping: send datagrams into a daemon listen_app; optional path kill."""

from __future__ import annotations

import argparse
import json
import select
import socket
import struct
import sys
import time

from daemon import parse_addr

WARMUP_SEQ = 0xFFFFFFFF
_SEQ = struct.Struct("<I")


def _payload(seq: int, size: int) -> bytes:
    body = _SEQ.pack(seq)
    if size <= len(body):
        return body
    return body + b"p" * (size - len(body))


def _seq_of(data: bytes) -> int | None:
    if len(data) < _SEQ.size:
        return None
    return _SEQ.unpack_from(data)[0]


def _recv_until(
    sock: socket.socket,
    timeout: float,
    on_data,
) -> None:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining < 0:
            remaining = 0.0
        try:
            readable, _, _ = select.select([sock], [], [], remaining)
        except (OSError, ValueError):
            return
        if not readable:
            return
        try:
            data, _addr = sock.recvfrom(65535)
        except BlockingIOError:
            return
        except OSError:
            return
        on_data(data)
        if timeout == 0:
            continue


def _send_kill(control: tuple[str, int], path: str, timeout: float = 0.5) -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(f"down {path}\n".encode(), control)
        try:
            data, _ = sock.recvfrom(4096)
            return data.decode("utf-8", errors="replace").strip()
        except TimeoutError:
            return "timeout"
        except OSError as exc:
            return f"error:{exc}"
    finally:
        sock.close()


def _wait_ready(sock: socket.socket, target: tuple[str, int], timeout: float, size: int) -> bool:
    deadline = time.monotonic() + timeout
    got = {"ok": False}

    def on_data(data: bytes) -> None:
        if _seq_of(data) == WARMUP_SEQ:
            got["ok"] = True

    while time.monotonic() < deadline and not got["ok"]:
        try:
            sock.sendto(_payload(WARMUP_SEQ, size), target)
        except OSError:
            pass
        _recv_until(sock, 0.05, on_data)
    return got["ok"]


def run_reflect(bind: tuple[str, int], target: tuple[str, int]) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(bind)
    print(f"mlink-ping --reflect bind={bind[0]}:{bind[1]} -> {target[0]}:{target[1]}", flush=True)
    try:
        while True:
            data, _addr = sock.recvfrom(65535)
            sock.sendto(data, target)
    except KeyboardInterrupt:
        return 0
    finally:
        sock.close()


def run_ping(
    *,
    bind: tuple[str, int],
    target: tuple[str, int],
    count: int,
    interval_s: float,
    payload_size: int,
    wait_s: float,
    ready_s: float,
    kill_after: int | None,
    kill_path: str | None,
    control: tuple[str, int] | None,
) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(bind)
    sock.setblocking(False)

    arrivals: dict[int, float] = {}
    times: list[float] = []

    def on_data(data: bytes) -> None:
        seq = _seq_of(data)
        if seq is None or seq == WARMUP_SEQ:
            return
        if seq in arrivals:
            return
        now = time.monotonic()
        arrivals[seq] = now
        times.append(now)

    try:
        if ready_s > 0 and not _wait_ready(sock, target, ready_s, payload_size):
            print("mlink-ping: no echo during warmup (is op/edge running?)", file=sys.stderr)
            return 1

        kill_reply = None
        t0 = time.monotonic()
        for seq in range(1, count + 1):
            sock.sendto(_payload(seq, payload_size), target)
            if kill_after is not None and seq == kill_after and kill_path and control:
                kill_reply = _send_kill(control, kill_path)
            _recv_until(sock, 0.0, on_data)
            if interval_s > 0:
                _recv_until(sock, interval_s, on_data)
        _recv_until(sock, wait_s, on_data)
        elapsed_s = time.monotonic() - t0
    finally:
        sock.close()

    delivered = sum(1 for s in range(1, count + 1) if s in arrivals)
    loss = 0.0 if count == 0 else 1.0 - (delivered / count)
    gaps = [t1 - t0_ for t0_, t1 in zip(times, times[1:])]
    max_gap_ms = (max(gaps) * 1000.0) if gaps else 0.0
    report = {
        "sent": count,
        "delivered": delivered,
        "loss": round(loss, 6),
        "max_gap_ms": round(max_gap_ms, 3),
        "elapsed_s": round(elapsed_s, 3),
        "kill_path": kill_path,
        "kill_after": kill_after,
        "kill_reply": kill_reply,
    }
    print(
        f"sent={count} delivered={delivered} loss={loss:.4f} "
        f"max_gap_ms={max_gap_ms:.3f}"
        + (
            f" kill={kill_path} after={kill_after} reply={kill_reply}"
            if kill_path
            else ""
        ),
        flush=True,
    )
    print(json.dumps(report), flush=True)
    return 0 if delivered == count else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mlink-ping")
    parser.add_argument(
        "--bind",
        default="127.0.0.1:5502",
        help="local UDP bind (op send_app, or edge send_app with --reflect)",
    )
    parser.add_argument(
        "--target",
        default="127.0.0.1:5501",
        help="daemon listen_app (op by default; edge listen_app with --reflect)",
    )
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--interval-ms", type=float, default=1.0)
    parser.add_argument("--payload-size", type=int, default=16)
    parser.add_argument("--wait-ms", type=float, default=500.0)
    parser.add_argument("--ready-timeout-s", type=float, default=2.0)
    parser.add_argument(
        "--kill-after",
        type=int,
        default=None,
        help="send 'down <path>' to --control after this many datagrams",
    )
    parser.add_argument("--kill-path", default=None, help="path name to disable (e.g. eth)")
    parser.add_argument(
        "--control",
        default=None,
        help="daemon --control host:port (required with --kill-path)",
    )
    parser.add_argument(
        "--reflect",
        action="store_true",
        help="bind --bind and echo every datagram to --target (far-side reflector)",
    )
    args = parser.parse_args(argv)

    bind = parse_addr(args.bind)
    target = parse_addr(args.target)
    if args.reflect:
        return run_reflect(bind, target)
    if args.count < 1:
        parser.error("--count must be >= 1")
    if args.payload_size < 4:
        parser.error("--payload-size must be >= 4")
    if args.kill_path and not args.control:
        parser.error("--kill-path requires --control")
    if args.kill_path and args.kill_after is None:
        parser.error("--kill-path requires --kill-after")
    control = parse_addr(args.control) if args.control else None
    return run_ping(
        bind=bind,
        target=target,
        count=args.count,
        interval_s=args.interval_ms / 1000.0,
        payload_size=args.payload_size,
        wait_s=args.wait_ms / 1000.0,
        ready_s=args.ready_timeout_s,
        kill_after=args.kill_after,
        kill_path=args.kill_path,
        control=control,
    )


if __name__ == "__main__":
    sys.exit(main())
