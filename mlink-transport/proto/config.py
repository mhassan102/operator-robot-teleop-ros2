"""YAML link list. Adding a path is config only (no protocol change)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


class ConfigError(ValueError):
    """Invalid mlink config."""


@dataclass(frozen=True)
class PathConfig:
    name: str
    bind_ip: str
    peer_ip: str
    peer_port: int
    ifname: str | None = None
    bind_port: int | None = None  # default: same as peer_port


@dataclass(frozen=True)
class MlinkConfig:
    session_id: int
    paths: tuple[PathConfig, ...]
    listen_app: str = "127.0.0.1:5501"
    send_app: str = "127.0.0.1:5502"
    heartbeat_interval_us: int = 100_000
    down_timeout_us: int = 300_000
    probe_interval_us: int = 1_000_000
    loss_threshold: float = 0.20
    loss_window: int = 20
    dedupe_window: int = 1024
    rtt_alpha: float = 0.2
    max_payload: int = 1440
    max_media_queue: int = 64


def load_config(source: str | Path | Mapping[str, Any]) -> MlinkConfig:
    if isinstance(source, Mapping):
        raw = dict(source)
    else:
        text = Path(source).read_text(encoding="utf-8")
        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise ConfigError("config root must be a mapping")
        raw = loaded
    return _parse(raw)


def _parse(raw: Mapping[str, Any]) -> MlinkConfig:
    try:
        session_id = int(raw["session_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError("session_id must be an integer") from exc
    if session_id < 0 or session_id > 0xFFFFFFFF:
        raise ConfigError("session_id must fit in u32")

    paths_raw = raw.get("paths")
    if not isinstance(paths_raw, list) or not paths_raw:
        raise ConfigError("paths must be a non-empty list")
    if len(paths_raw) > 256:
        raise ConfigError("at most 256 paths (path_id is u8)")

    paths: list[PathConfig] = []
    names: set[str] = set()
    for item in paths_raw:
        if not isinstance(item, dict):
            raise ConfigError("each path must be a mapping")
        path = _parse_path(item)
        if path.name in names:
            raise ConfigError(f"duplicate path name {path.name!r}")
        names.add(path.name)
        paths.append(path)

    cfg = MlinkConfig(
        session_id=session_id,
        paths=tuple(paths),
        listen_app=str(raw.get("listen_app", "127.0.0.1:5501")),
        send_app=str(raw.get("send_app", "127.0.0.1:5502")),
        heartbeat_interval_us=int(raw.get("heartbeat_interval_us", 100_000)),
        down_timeout_us=int(raw.get("down_timeout_us", 300_000)),
        probe_interval_us=int(raw.get("probe_interval_us", 1_000_000)),
        loss_threshold=float(raw.get("loss_threshold", 0.20)),
        loss_window=int(raw.get("loss_window", 20)),
        dedupe_window=int(raw.get("dedupe_window", 1024)),
        rtt_alpha=float(raw.get("rtt_alpha", 0.2)),
        max_payload=int(raw.get("max_payload", 1440)),
        max_media_queue=int(raw.get("max_media_queue", 64)),
    )
    if cfg.heartbeat_interval_us <= 0 or cfg.down_timeout_us <= 0:
        raise ConfigError("heartbeat_interval_us and down_timeout_us must be > 0")
    if cfg.probe_interval_us <= 0:
        raise ConfigError("probe_interval_us must be > 0")
    if not (0.0 <= cfg.loss_threshold <= 1.0):
        raise ConfigError("loss_threshold must be in [0, 1]")
    if cfg.loss_window < 1 or cfg.dedupe_window < 1:
        raise ConfigError("loss_window and dedupe_window must be >= 1")
    return cfg


def _parse_path(item: Mapping[str, Any]) -> PathConfig:
    name = str(item.get("name") or "").strip()
    if not name:
        raise ConfigError("path.name is required")
    ifname = item.get("ifname")
    if ifname is not None:
        ifname = str(ifname).strip() or None
    if ifname is not None and ifname.lower() == "tailscale0":
        raise ConfigError("tailscale0 is SSH/management only; do not bind it")

    bind_ip = str(item.get("bind_ip") or "").strip()
    if not bind_ip:
        raise ConfigError(f"path {name!r} missing bind_ip")
    _reject_tailscale_ip(bind_ip, f"path {name!r} bind_ip")

    peer = item.get("peer")
    if not isinstance(peer, str) or ":" not in peer:
        raise ConfigError(f"path {name!r} peer must be host:port")
    peer_ip, _, port_s = peer.rpartition(":")
    peer_ip = peer_ip.strip()
    try:
        peer_port = int(port_s)
    except ValueError as exc:
        raise ConfigError(f"path {name!r} peer port is not an integer") from exc
    if not peer_ip:
        raise ConfigError(f"path {name!r} peer host is empty")
    if peer_port < 1 or peer_port > 65535:
        raise ConfigError(f"path {name!r} peer port out of range")
    _reject_tailscale_ip(peer_ip, f"path {name!r} peer")

    bind_port_raw = item.get("bind_port")
    bind_port = int(bind_port_raw) if bind_port_raw is not None else None
    if bind_port is not None and (bind_port < 1 or bind_port > 65535):
        raise ConfigError(f"path {name!r} bind_port out of range")

    return PathConfig(
        name=name,
        bind_ip=bind_ip,
        peer_ip=peer_ip,
        peer_port=peer_port,
        ifname=ifname,
        bind_port=bind_port,
    )


def _reject_tailscale_ip(ip: str, label: str) -> None:
    # Locked decision: never use 100.x peer IPs (Tailscale).
    if ip.startswith("100."):
        raise ConfigError(f"{label} {ip} looks like Tailscale; not a bonded path")
