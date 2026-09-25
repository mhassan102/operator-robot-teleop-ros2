"""NAT mapping class, idle lifetime, and keepalive policy (T6).

Two STUN Binding samples from one UDP socket. A missing second
response is ``untested``. Do not invent a finer class than those
two samples support.

The idle-lifetime probe and the refresh soak are manual. Default
pytest must not call them. aioice consent (~5s) and the TURN
allocation refresh stay as they are; this module does not send on
the aioice socket.
"""

from __future__ import annotations

import ipaddress
import socket
import sys
import time
import warnings
from dataclasses import dataclass
from typing import Any, Callable

from aioice import stun
from aioice.ice import CONSENT_INTERVAL

from .ice import bind_to_device, validate_ice_endpoints

DEFAULT_KEEPALIVE_S = 15
COTURN_STUN_PORT = 3478
COTURN_RELAY_LO = 50000
COTURN_RELAY_HI = 50100

Mapped = tuple[str, int]
Dest = tuple[str, int]


@dataclass(frozen=True)
class NatSample:
    nat_class: str
    detail: str
    mapped1: Mapped
    mapped2: Mapped | None
    dest1: Dest
    dest2: Dest | None


def is_coturn_port(port: int) -> bool:
    """Ports coturn already owns in this lab. stun-respond must not take them."""
    return port == COTURN_STUN_PORT or COTURN_RELAY_LO <= port <= COTURN_RELAY_HI


def is_stun_datagram(data: bytes) -> bool:
    if len(data) < 20 or data[0] & 0xC0:
        return False
    cookie = int.from_bytes(data[4:8], "big")
    return cookie == stun.COOKIE


def binding_response(data: bytes, mapped: Mapped) -> bytes | None:
    """Binding success with XOR-MAPPED-ADDRESS, or None if ``data`` is not a request."""
    try:
        msg = stun.parse_message(data)
    except ValueError:
        return None
    if msg.message_method != stun.Method.BINDING or msg.message_class != stun.Class.REQUEST:
        return None
    resp = stun.Message(
        stun.Method.BINDING,
        stun.Class.RESPONSE,
        transaction_id=msg.transaction_id,
    )
    resp.attributes["XOR-MAPPED-ADDRESS"] = (mapped[0], int(mapped[1]))
    return bytes(resp)


def _xor_mapped(data: bytes, transaction_id: bytes) -> Mapped | None:
    try:
        msg = stun.parse_message(data)
    except ValueError:
        return None
    if msg.transaction_id != transaction_id:
        return None
    if msg.message_method != stun.Method.BINDING:
        return None
    if msg.message_class == stun.Class.ERROR:
        raise ValueError("STUN Binding error response")
    if msg.message_class != stun.Class.RESPONSE:
        return None
    addr = msg.attributes.get("XOR-MAPPED-ADDRESS")
    if not isinstance(addr, tuple) or len(addr) != 2:
        raise ValueError("STUN Binding success has no XOR-MAPPED-ADDRESS")
    return (str(addr[0]), int(addr[1]))


def stun_binding(sock: socket.socket, dest: Dest, timeout: float = 3.0) -> Mapped:
    """One Binding transaction. Retransmits the same request until ``timeout``."""
    request = stun.Message(stun.Method.BINDING, stun.Class.REQUEST)
    payload = bytes(request)
    deadline = time.monotonic() + timeout
    next_send = 0.0
    while True:
        now = time.monotonic()
        if now >= deadline:
            raise TimeoutError(f"STUN Binding timeout {dest[0]}:{dest[1]}")
        if now >= next_send:
            sock.sendto(payload, dest)
            next_send = now + 0.5
        sock.settimeout(max(0.01, min(0.5, deadline - time.monotonic())))
        try:
            data, _addr = sock.recvfrom(2048)
        except socket.timeout:
            continue
        mapped = _xor_mapped(data, request.transaction_id)
        if mapped is not None:
            return mapped


def _is_100_x(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return ip.version == 4 and str(ip).startswith("100.")


def resolve_stun(host: str, port: int) -> Dest:
    if _is_100_x(host):
        raise ValueError("stun server is 100.x; Tailscale is SSH/mgmt only")
    try:
        infos = socket.getaddrinfo(str(host), int(port), socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as exc:
        raise ValueError(f"cannot resolve STUN server {host}:{port}") from exc
    ip = str(infos[0][4][0])
    if _is_100_x(ip):
        raise ValueError("stun server is 100.x; Tailscale is SSH/mgmt only")
    return (ip, int(port))


def stun_endpoints(cfg: dict[str, Any]) -> tuple[Dest, Dest | None]:
    """Primary STUN dest, and the optional second dest. The two must differ."""
    host = cfg.get("stun_server")
    if host is None or host == "":
        raise ValueError("config missing stun_server")
    dest1 = resolve_stun(str(host), int(cfg.get("stun_port") or 3478))
    host2 = cfg.get("stun_server_2")
    if host2 is None or host2 == "":
        return dest1, None
    if cfg.get("stun_port_2") in (None, ""):
        raise ValueError("stun_server_2 is set but stun_port_2 is missing")
    dest2 = resolve_stun(str(host2), int(cfg["stun_port_2"]))
    if dest1 == dest2:
        raise ValueError("stun_server and stun_server_2 must differ")
    return dest1, dest2


def classify_mapping(
    mapped1: Mapped,
    mapped2: Mapped | None,
    dest1: Dest,
    dest2: Dest | None,
) -> tuple[str, str]:
    """Return ``(class, detail)`` from two XOR-MAPPED-ADDRESS samples.

    ``port-independent`` is the same mapped ip:port toward one server IP
    on two ports. That is not a two-IP proof; the printed class is still
    ``endpoint-independent``. A port change toward two different server
    IPs, with no same-IP sample, is ``address-dependent-unconfirmed``.
    """
    if mapped2 is None or dest2 is None:
        return "untested", "untested"
    if dest1 == dest2:
        raise ValueError("stun destinations must differ")
    same_map = mapped1 == mapped2
    same_ip = dest1[0] == dest2[0]
    if same_map and not same_ip:
        return "endpoint-independent", "endpoint-independent"
    if same_map and same_ip:
        return "endpoint-independent", "port-independent"
    if same_ip:
        return "endpoint-dependent", "address-and-port-dependent"
    return "endpoint-dependent", "address-dependent-unconfirmed"


def format_nat_line(sample: NatSample) -> str:
    mapped2 = "missing" if sample.mapped2 is None else f"{sample.mapped2[0]}:{sample.mapped2[1]}"
    dest2 = "missing" if sample.dest2 is None else f"{sample.dest2[0]}:{sample.dest2[1]}"
    return (
        f"nat class={sample.nat_class} detail={sample.detail} "
        f"mapped1={sample.mapped1[0]}:{sample.mapped1[1]} mapped2={mapped2} "
        f"dest1={sample.dest1[0]}:{sample.dest1[1]} dest2={dest2}"
    )


def parse_keepalive_s(cfg: dict[str, Any]) -> int | None:
    if "keepalive_s" not in cfg:
        return None
    value = cfg["keepalive_s"]
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("keepalive_s must be a positive integer")
    return value


def parse_mapping_ttl(cfg: dict[str, Any]) -> int | None:
    """Measured idle TTL from a manual nat-ttl run. Absent means unknown."""
    if "mapping_ttl_s" not in cfg:
        return None
    value = cfg["mapping_ttl_s"]
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("mapping_ttl_s must be an integer number of seconds")
    return value


def keepalive_interval(configured_s: int | None, ttl_s: int | None) -> tuple[int, str]:
    """NAT-mapping policy. Does not send.

    Unknown TTL returns the configured interval (default 15s).
    A measured TTL returns ``min(configured, max(1, ttl//2))``, which
    is below the TTL when ``ttl > 1``. ``ttl <= 1`` returns 1 and warns.
    """
    configured = DEFAULT_KEEPALIVE_S if configured_s is None else configured_s
    if isinstance(configured, bool) or not isinstance(configured, int) or configured < 1:
        raise ValueError("keepalive_s must be a positive integer")
    if ttl_s is None:
        return configured, "yaml-default"
    if isinstance(ttl_s, bool) or not isinstance(ttl_s, int):
        raise ValueError("mapping ttl must be an integer number of seconds")
    if ttl_s <= 1:
        warnings.warn(
            "measured mapping ttl <= 1s; keepalive interval is 1s",
            UserWarning,
            stacklevel=2,
        )
        return 1, "measured-ttl"
    return min(configured, max(1, ttl_s // 2)), "measured-ttl"


def consent_warning(ttl_s: int | None, consent_s: int | None = None) -> str | None:
    """Warn when aioice consent is not shorter than a measured mapping TTL."""
    if ttl_s is None:
        return None
    limit = int(CONSENT_INTERVAL if consent_s is None else consent_s)
    if ttl_s > limit:
        return None
    return f"consent interval {limit}s is not below measured mapping ttl {ttl_s}s"


def format_keepalive_line(path_kind: str, interval_s: int, basis: str, ttl_s: int | None) -> str:
    shown = "unknown" if ttl_s is None else str(ttl_s)
    return (
        f"keepalive path={path_kind} interval_s={interval_s} "
        f"basis={basis} ttl_s={shown} consent_s={int(CONSENT_INTERVAL)}"
    )


def print_keepalive(path_kind: str, cfg: dict[str, Any]) -> None:
    """Print the NAT-mapping policy. Does not refresh the aioice socket."""
    ttl = parse_mapping_ttl(cfg)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", UserWarning)
        interval, basis = keepalive_interval(parse_keepalive_s(cfg), ttl)
    print(format_keepalive_line(path_kind, interval, basis, ttl), flush=True)
    for item in caught:
        print(f"warning: {item.message}", file=sys.stderr, flush=True)
    note = consent_warning(ttl)
    if note:
        print(f"warning: {note}", file=sys.stderr, flush=True)


def _ifname(cfg: dict[str, Any]) -> str | None:
    value = cfg.get("ifname")
    if value is None or value == "":
        return None
    return str(value)


def prepare(cfg: dict[str, Any]) -> tuple[str, str | None, Dest, Dest | None]:
    bind_ip = str(cfg["bind_ip"])
    ifname = _ifname(cfg)
    validate_ice_endpoints(bind_ip, ifname, str(cfg.get("stun_server") or ""), None)
    host2 = cfg.get("stun_server_2")
    if host2 not in (None, ""):
        validate_ice_endpoints(bind_ip, ifname, str(host2), None)
    dest1, dest2 = stun_endpoints(cfg)
    return bind_ip, ifname, dest1, dest2


def open_udp(bind_ip: str, ifname: str | None) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((bind_ip, 0))
    except OSError:
        sock.close()
        raise
    if ifname:
        try:
            bind_to_device(sock, ifname)
        except OSError as exc:
            print(
                f"note: SO_BINDTODEVICE {ifname!r} failed ({exc.strerror}); "
                "socket bound to bind_ip only",
                file=sys.stderr,
                flush=True,
            )
    return sock


def probe_mapping(
    sock: socket.socket,
    dest1: Dest,
    dest2: Dest | None,
    timeout: float = 3.0,
) -> NatSample:
    """Two Bindings on ``sock``. A missing second response is untested."""
    mapped1 = stun_binding(sock, dest1, timeout)
    mapped2: Mapped | None = None
    if dest2 is not None:
        try:
            mapped2 = stun_binding(sock, dest2, timeout)
        except TimeoutError:
            mapped2 = None
    nat_class, detail = classify_mapping(mapped1, mapped2, dest1, dest2)
    return NatSample(nat_class, detail, mapped1, mapped2, dest1, dest2)


def wait_non_stun(sock: socket.socket, seconds: float) -> bool:
    """True if a non-STUN datagram arrives within ``seconds``."""
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        sock.settimeout(remaining)
        try:
            data, _addr = sock.recvfrom(2048)
        except socket.timeout:
            return False
        if is_stun_datagram(data):
            continue
        return True


def nat_check(cfg: dict[str, Any], listen_s: float = 0) -> int:
    """Print the class line, then optionally wait for one non-STUN datagram.

    The class line is printed before the wait so the mapped address is
    visible while the socket stays open.
    """
    if listen_s < 0:
        raise ValueError("--listen-s must be >= 0")
    bind_ip, ifname, dest1, dest2 = prepare(cfg)
    sock = open_udp(bind_ip, ifname)
    try:
        sample = probe_mapping(sock, dest1, dest2)
        print(format_nat_line(sample), flush=True)
        if listen_s > 0:
            got = wait_non_stun(sock, listen_s)
            print(f"nat filtering={'received' if got else 'none'}", flush=True)
    finally:
        sock.close()
    return 0


def parse_holds(text: str) -> list[int]:
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if not parts:
        raise ValueError("--holds needs one or more positive seconds")
    holds: list[int] = []
    for part in parts:
        if not part.isdigit() or int(part) < 1:
            raise ValueError(f"hold {part!r} must be a positive integer number of seconds")
        holds.append(int(part))
    return holds


def ttl_summary(changed_hold: int | None, holds: list[int]) -> str:
    if changed_hold is not None:
        return f"nat_ttl changed_after={changed_hold}"
    return f"nat_ttl inconclusive_within={max(holds)}"


def _idle_once(bind_ip: str, ifname: str | None, dest: Dest, hold_s: int) -> tuple[Mapped, Mapped]:
    """Fresh socket: Binding, silence for ``hold_s``, one more Binding."""
    sock = open_udp(bind_ip, ifname)
    try:
        before = stun_binding(sock, dest)
        time.sleep(hold_s)
        after = stun_binding(sock, dest)
        return before, after
    finally:
        sock.close()


def _fmt_mapped(mapped: Mapped) -> str:
    return f"{mapped[0]}:{mapped[1]}"


def run_holds(cfg: dict[str, Any], holds: list[int]) -> int:
    """One fresh socket per hold, toward the primary STUN server only.

    A changed mapped port means that idle mapping died. An unchanged
    port is inconclusive: a port-preserving NAT can reuse the port.
    """
    if not holds:
        raise ValueError("--holds needs one or more positive seconds")
    bind_ip, ifname, dest1, _dest2 = prepare(cfg)
    for hold in holds:
        print(f"nat_ttl hold={hold} start", flush=True)
        before, after = _idle_once(bind_ip, ifname, dest1, hold)
        changed = before[1] != after[1]
        state = "port_changed" if changed else "port_same"
        print(
            f"nat_ttl hold={hold} mapped_before={_fmt_mapped(before)} "
            f"mapped_after={_fmt_mapped(after)} {state}",
            flush=True,
        )
        if changed:
            print(ttl_summary(hold, holds), flush=True)
            return 0
    print(ttl_summary(None, holds), flush=True)
    print(
        "note: mapped port unchanged; a port-preserving NAT can reuse the port",
        file=sys.stderr,
        flush=True,
    )
    return 0


def _refresh_changed(interval: int, duration: int, before: Mapped, after: Mapped) -> None:
    print(
        f"nat_ttl refresh changed interval_s={interval} duration_s={duration} "
        f"mapped_before={_fmt_mapped(before)} mapped_after={_fmt_mapped(after)}",
        flush=True,
    )


def run_refresh(cfg: dict[str, Any], interval_s: int, duration_s: int) -> int:
    """STUN Binding every ``interval_s`` for ``duration_s``. Pass if the port holds.

    Lab soak only. Not for pytest.
    """
    if interval_s < 1 or duration_s < 1:
        raise ValueError("--refresh and --duration must be >= 1 second")
    bind_ip, ifname, dest1, _dest2 = prepare(cfg)
    sock = open_udp(bind_ip, ifname)
    try:
        start = stun_binding(sock, dest1)
        print(
            f"nat_ttl refresh start mapped={_fmt_mapped(start)} "
            f"interval_s={interval_s} duration_s={duration_s}",
            flush=True,
        )
        started = time.monotonic()
        deadline = started + duration_s
        next_send = started + interval_s
        current = start
        while next_send <= deadline + 0.001:
            delay = next_send - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            current = stun_binding(sock, dest1)
            if current[1] != start[1]:
                _refresh_changed(interval_s, duration_s, start, current)
                return 1
            next_send += interval_s
        tail = deadline - time.monotonic()
        if tail > 0.05:
            time.sleep(tail)
            current = stun_binding(sock, dest1)
            if current[1] != start[1]:
                _refresh_changed(interval_s, duration_s, start, current)
                return 1
        print(
            f"nat_ttl refresh unchanged interval_s={interval_s} "
            f"duration_s={duration_s} mapped={_fmt_mapped(current)}",
            flush=True,
        )
        return 0
    finally:
        sock.close()


def serve_binding(
    sock: socket.socket,
    mapped_for: Callable[[tuple[str, int]], Mapped] | None = None,
) -> None:
    """Answer Binding until the socket is closed. Not coturn."""
    while True:
        try:
            data, addr = sock.recvfrom(2048)
        except OSError:
            return
        mapped = addr if mapped_for is None else mapped_for(addr)
        reply = binding_response(data, (str(mapped[0]), int(mapped[1])))
        if reply is None:
            continue
        try:
            sock.sendto(reply, addr)
        except OSError:
            return


def run_stun_respond(bind_host: str, port: int) -> int:
    if is_coturn_port(port):
        print("stun-respond must not bind 3478 or 50000-50100", file=sys.stderr)
        return 2
    if _is_100_x(bind_host):
        print("stun-respond bind is 100.x; Tailscale is SSH/mgmt only", file=sys.stderr)
        return 2
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((bind_host, port))
    except OSError as exc:
        print(f"stun-respond bind {bind_host}:{port} failed: {exc}", file=sys.stderr)
        sock.close()
        return 1
    print(f"stun-respond {bind_host}:{port}", flush=True)
    try:
        serve_binding(sock)
    finally:
        sock.close()
    return 0
