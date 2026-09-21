"""ICE agent: STUN gather (T3) and direct punch / echo (T4). No TURN Allocate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import errno
import ipaddress
import socket

import yaml
from aioice import Candidate, Connection

HELLO = b"hello"
HELLO_ACK = b"hello-ack"
DIRECT_TYPES = frozenset({"host", "srflx", "prflx"})

# Tailscale is SSH/mgmt only (locked decision 5).
_TAILSCALE_IFNAME = "tailscale0"


def load_config(path: Path | str) -> dict[str, Any]:
    cfg_path = Path(path)
    with cfg_path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"config {cfg_path} must be a mapping")
    for key in ("role", "bind_ip", "stun_server", "stun_port"):
        if key not in cfg:
            raise ValueError(f"config missing {key}")
    if cfg["role"] not in ("controlling", "controlled"):
        raise ValueError("role must be controlling or controlled")
    return cfg


def candidate_to_dict(candidate: Any) -> dict[str, Any]:
    """Structured candidate for print / later signalling (T2 blob keys)."""
    out: dict[str, Any] = {
        "ip": candidate.host,
        "port": int(candidate.port),
        "type": candidate.type,
        "foundation": str(candidate.foundation),
        "component": int(candidate.component),
        "priority": int(candidate.priority),
    }
    related_ip = getattr(candidate, "related_address", None)
    related_port = getattr(candidate, "related_port", None)
    if related_ip is not None and related_port is not None:
        out["related"] = {"ip": related_ip, "port": int(related_port)}
    return out


def format_candidate(c: dict[str, Any]) -> str:
    line = f"{c['type']} {c['ip']}:{c['port']}"
    related = c.get("related")
    if related is not None:
        line += f" related {related['ip']}:{related['port']}"
    return line


def dict_to_candidate(c: dict[str, Any]) -> Candidate:
    """Rebuild an aioice Candidate from a signalling / print dict."""
    related = c.get("related") or {}
    related_ip = related.get("ip")
    related_port = related.get("port")
    return Candidate(
        foundation=str(c["foundation"]),
        component=int(c["component"]),
        transport="udp",
        priority=int(c["priority"]),
        host=str(c["ip"]),
        port=int(c["port"]),
        type=str(c["type"]),
        related_address=str(related_ip) if related_ip is not None else None,
        related_port=int(related_port) if related_port is not None else None,
    )


def nominated_path(connection: Connection) -> dict[str, Any]:
    """Classify the nominated pair. aioice has no public nominated-pair API."""
    pair = getattr(connection, "_nominated", {}).get(1)
    if pair is None:
        return {"kind": "none"}
    local = pair.local_candidate
    remote = pair.remote_candidate
    types = {local.type, remote.type}
    kind = "direct" if types <= DIRECT_TYPES else "turn"
    return {
        "kind": kind,
        "local_type": local.type,
        "remote_type": remote.type,
        "local": f"{local.host}:{local.port}",
        "remote": f"{remote.host}:{remote.port}",
    }


def format_path(info: dict[str, Any]) -> str:
    if info.get("kind") in (None, "none"):
        return "path=none"
    return (
        f"path={info['kind']} "
        f"local={info['local_type']}:{info['local']} "
        f"remote={info['remote_type']}:{info['remote']}"
    )


def _is_100_x(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return ip.version == 4 and str(ip).startswith("100.")


def validate_ice_endpoints(bind_ip: str, ifname: str | None, stun_server: str) -> None:
    if _is_100_x(bind_ip):
        raise ValueError("bind_ip is 100.x; Tailscale is SSH/mgmt only")
    if ifname == _TAILSCALE_IFNAME:
        raise ValueError("ifname is tailscale0; Tailscale is SSH/mgmt only")
    if stun_server and _is_100_x(str(stun_server)):
        raise ValueError("stun_server is 100.x; Tailscale is SSH/mgmt only")


def bind_to_device(sock: socket.socket, ifname: str) -> None:
    """SO_BINDTODEVICE on an existing UDP socket. May need CAP_NET_ADMIN."""
    if not ifname:
        return
    opt = getattr(socket, "SO_BINDTODEVICE", None)
    if opt is None:
        raise OSError(
            getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
            "SO_BINDTODEVICE is not available; leave ifname unset for bind_ip only",
        )
    try:
        sock.setsockopt(socket.SOL_SOCKET, opt, ifname.encode("ascii"))
    except OSError as exc:
        extra = ""
        if exc.errno == errno.EPERM:
            extra = " Need CAP_NET_ADMIN (CAP_NET_RAW on older kernels)."
        elif exc.errno == errno.ENODEV:
            extra = f" Interface {ifname!r} does not exist."
        raise OSError(
            exc.errno,
            f"SO_BINDTODEVICE {ifname!r} failed: {exc.strerror}.{extra}",
        ) from exc


def bound_device_name(sock: socket.socket) -> str | None:
    opt = getattr(socket, "SO_BINDTODEVICE", None)
    if opt is None:
        return None
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, opt, 16)
    except OSError:
        return None
    name = raw.split(b"\0", 1)[0].decode("ascii", errors="replace")
    return name or None


def _ifname(cfg: dict[str, Any]) -> str | None:
    value = cfg.get("ifname")
    if value is None or value == "":
        return None
    return str(value)


def _stun_server(cfg: dict[str, Any]) -> tuple[str, int] | None:
    host = cfg.get("stun_server")
    if host is None or host == "":
        return None
    return (str(host), int(cfg["stun_port"]))


def _apply_bindtodevice(connection: Connection, ifname: str | None) -> str | None:
    """aioice has no ifname argument; apply SO_BINDTODEVICE after it binds bind_ip."""
    if not ifname:
        return None
    opt = getattr(socket, "SO_BINDTODEVICE", None)
    if opt is None:
        return (
            "aioice has no ifname hook; SO_BINDTODEVICE unavailable, "
            "sockets bound to bind_ip only"
        )
    protocols = getattr(connection, "_protocols", [])
    applied = 0
    last_err: str | None = None
    for proto in protocols:
        transport = getattr(proto, "transport", None)
        if transport is None:
            continue
        sock = transport.get_extra_info("socket")
        if sock is None:
            continue
        try:
            bind_to_device(sock, ifname)
            applied += 1
        except OSError as exc:
            last_err = (
                f"SO_BINDTODEVICE {ifname!r} failed ({exc.strerror}); "
                "sockets bound to bind_ip only"
            )
    if last_err is not None:
        return last_err
    if applied == 0:
        return (
            "aioice has no ifname hook; no gather sockets for SO_BINDTODEVICE, "
            "bound bind_ip only"
        )
    return None


class IceAgent:
    """Same code on operator and robot. Gather (T3) then punch/echo (T4). No TURN."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.bind_note: str | None = None
        self.connection: Connection | None = None
        validate_ice_endpoints(
            str(cfg["bind_ip"]),
            _ifname(cfg),
            str(cfg.get("stun_server") or ""),
        )

    def build_connection(self) -> Connection:
        # T3/T4: STUN only. Do not pass turn_server (no Allocate; T5).
        return Connection(
            ice_controlling=self.cfg["role"] == "controlling",
            stun_server=_stun_server(self.cfg),
            use_ipv4=True,
            use_ipv6=False,
        )

    async def gather(self) -> list[dict[str, Any]]:
        bind_ip = str(self.cfg["bind_ip"])
        ifname = _ifname(self.cfg)
        conn = self.build_connection()
        self.connection = conn
        orig = conn.get_component_candidates

        async def _one_nic(
            component: int, addresses: list[str], timeout: int = 5
        ) -> list[Any]:
            del addresses
            cands = await orig(component, [bind_ip], timeout)
            self.bind_note = _apply_bindtodevice(conn, ifname)
            return cands

        conn.get_component_candidates = _one_nic  # type: ignore[method-assign]
        await conn.gather_candidates()
        return [candidate_to_dict(c) for c in conn.local_candidates]

    async def set_remote(self, blob: dict[str, Any]) -> None:
        """Install peer ICE-UFRAG / ICE-PWD and candidates (authenticated checks)."""
        conn = self.connection
        if conn is None:
            raise RuntimeError("gather first")
        username = blob.get("username")
        password = blob.get("password")
        if not isinstance(username, str) or not username:
            raise ValueError("peer blob missing username")
        if not isinstance(password, str) or not password:
            raise ValueError("peer blob missing password")
        conn.remote_username = username
        conn.remote_password = password
        for raw in blob.get("candidates") or []:
            await conn.add_remote_candidate(dict_to_candidate(raw))
        await conn.add_remote_candidate(None)

    async def connect(self) -> None:
        """ICE connectivity checks + nominate. Consent keepalive starts in aioice."""
        if self.connection is None:
            raise RuntimeError("gather first")
        await self.connection.connect()

    def path(self) -> dict[str, Any]:
        if self.connection is None:
            return {"kind": "none"}
        return nominated_path(self.connection)

    async def send(self, data: bytes) -> None:
        if self.connection is None:
            raise RuntimeError("not connected")
        await self.connection.send(data)

    async def recv(self) -> bytes:
        if self.connection is None:
            raise RuntimeError("not connected")
        return await self.connection.recv()

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()
            self.connection = None


async def gather(cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    agent = IceAgent(cfg)
    try:
        cands = await agent.gather()
        return cands, agent.bind_note
    finally:
        await agent.close()
