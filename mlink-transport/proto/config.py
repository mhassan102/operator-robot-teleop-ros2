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
    # udp: bind + sendto(peer). ice: WAN is the nominated aioice connection.
    transport: str = "udp"
    ice_config: str = ""


@dataclass(frozen=True)
class MlinkConfig:
    session_id: int
    paths: tuple[PathConfig, ...]
    listen_app: str = "127.0.0.1:5501"
    send_app: str = "127.0.0.1:5502"
    listen_media: str = ""
    send_media: str = ""
    heartbeat_interval_us: int = 100_000
    down_timeout_us: int = 300_000
    probe_interval_us: int = 1_000_000
    loss_threshold: float = 0.20
    loss_window: int = 20
    dedupe_window: int = 1024
    rtt_alpha: float = 0.2
    max_payload: int = 1440
    max_media_queue: int = 64
    # Session opt-in. Default False: reject tailscale0 / 100.x (Orin lab).
    allow_tailscale: bool = False


def load_config(source: str | Path | Mapping[str, Any]) -> MlinkConfig:
    if isinstance(source, Mapping):
        raw = dict(source)
    else:
        text = Path(source).read_text(encoding="utf-8")
        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise ConfigError("config root must be a mapping")
        raw = loaded
        base = Path(source).resolve().parent
        return _parse(raw, base_dir=base)
    return _parse(raw, base_dir=None)


def _parse(raw: Mapping[str, Any], *, base_dir: Path | None) -> MlinkConfig:
    try:
        session_id = int(raw["session_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError("session_id must be an integer") from exc
    if session_id < 0 or session_id > 0xFFFFFFFF:
        raise ConfigError("session_id must fit in u32")

    allow_raw = raw.get("allow_tailscale", False)
    if not isinstance(allow_raw, bool):
        raise ConfigError("allow_tailscale must be a boolean")
    allow_tailscale = allow_raw

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
        path = _parse_path(item, allow_tailscale=allow_tailscale, base_dir=base_dir)
        if path.name in names:
            raise ConfigError(f"duplicate path name {path.name!r}")
        names.add(path.name)
        paths.append(path)

    listen_app = _require_app_addr(
        raw.get("listen_app", "127.0.0.1:5501"),
        "listen_app",
        allow_tailscale=allow_tailscale,
    )
    send_app = _require_app_addr(
        raw.get("send_app", "127.0.0.1:5502"),
        "send_app",
        allow_tailscale=allow_tailscale,
    )
    listen_media = _optional_app_addr(
        raw.get("listen_media"), "listen_media", allow_tailscale=allow_tailscale
    )
    send_media = _optional_app_addr(
        raw.get("send_media"), "send_media", allow_tailscale=allow_tailscale
    )
    app_addrs = [a for a in (listen_app, send_app, listen_media, send_media) if a]
    if len(set(app_addrs)) != len(app_addrs):
        raise ConfigError("listen_app/send_app/listen_media/send_media must be unique")

    cfg = MlinkConfig(
        session_id=session_id,
        paths=tuple(paths),
        listen_app=listen_app,
        send_app=send_app,
        listen_media=listen_media,
        send_media=send_media,
        heartbeat_interval_us=int(raw.get("heartbeat_interval_us", 100_000)),
        down_timeout_us=int(raw.get("down_timeout_us", 300_000)),
        probe_interval_us=int(raw.get("probe_interval_us", 1_000_000)),
        loss_threshold=float(raw.get("loss_threshold", 0.20)),
        loss_window=int(raw.get("loss_window", 20)),
        dedupe_window=int(raw.get("dedupe_window", 1024)),
        rtt_alpha=float(raw.get("rtt_alpha", 0.2)),
        max_payload=int(raw.get("max_payload", 1440)),
        max_media_queue=int(raw.get("max_media_queue", 64)),
        allow_tailscale=allow_tailscale,
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


def _parse_path(
    item: Mapping[str, Any],
    *,
    allow_tailscale: bool,
    base_dir: Path | None,
) -> PathConfig:
    name = str(item.get("name") or "").strip()
    if not name:
        raise ConfigError("path.name is required")
    transport = str(item.get("transport") or "udp").strip().lower()
    if transport not in ("udp", "ice"):
        raise ConfigError(f"path {name!r} transport must be udp or ice")
    if transport == "ice":
        return _parse_ice_path(item, name=name, base_dir=base_dir)
    ifname = item.get("ifname")
    if ifname is not None:
        ifname = str(ifname).strip() or None
    if (
        ifname is not None
        and ifname.lower() == "tailscale0"
        and not allow_tailscale
    ):
        raise ConfigError("tailscale0 is SSH/management only; do not bind it")

    bind_ip = str(item.get("bind_ip") or "").strip()
    if not bind_ip:
        raise ConfigError(f"path {name!r} missing bind_ip")
    _reject_tailscale_ip(
        bind_ip, f"path {name!r} bind_ip", allow_tailscale=allow_tailscale
    )

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
    _reject_tailscale_ip(
        peer_ip, f"path {name!r} peer", allow_tailscale=allow_tailscale
    )

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


def _parse_ice_path(
    item: Mapping[str, Any],
    *,
    name: str,
    base_dir: Path | None,
) -> PathConfig:
    """One nominated ICE connection. Addresses live in the ICE yaml, not here."""
    ifname = item.get("ifname")
    if ifname is not None and str(ifname).strip():
        if str(ifname).strip().lower() == "tailscale0":
            raise ConfigError("tailscale0 is SSH/management only; do not bind it")
        raise ConfigError(
            f"path {name!r} ifname belongs in the ICE config, not the mlink path"
        )
    if any(key in item for key in ("bind_ip", "bind_port", "peer")):
        raise ConfigError(
            f"path {name!r} transport ice has no UDP peer; "
            "bind_ip and TURN live in ice_config"
        )
    raw_cfg = item.get("ice_config")
    if not isinstance(raw_cfg, str) or not raw_cfg.strip():
        raise ConfigError(f"path {name!r} transport ice requires ice_config")
    ice_path = Path(raw_cfg.strip())
    if not ice_path.is_absolute():
        ice_path = (base_dir / ice_path).resolve() if base_dir is not None else ice_path.resolve()
    return PathConfig(
        name=name,
        bind_ip="0.0.0.0",
        peer_ip="0.0.0.0",
        peer_port=9,
        ifname=None,
        bind_port=None,
        transport="ice",
        ice_config=str(ice_path),
    )


def _require_app_addr(value: Any, label: str, *, allow_tailscale: bool) -> str:
    text = str(value or "").strip()
    if not text:
        raise ConfigError(f"{label} is required")
    return _parse_app_addr(text, label, allow_tailscale=allow_tailscale)


def _optional_app_addr(value: Any, label: str, *, allow_tailscale: bool) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return _parse_app_addr(text, label, allow_tailscale=allow_tailscale)


def _parse_app_addr(text: str, label: str, *, allow_tailscale: bool) -> str:
    host, sep, port_s = text.rpartition(":")
    if not sep or not host:
        raise ConfigError(f"{label} must be host:port")
    try:
        port = int(port_s)
    except ValueError as exc:
        raise ConfigError(f"{label} port is not an integer") from exc
    if port < 1 or port > 65535:
        raise ConfigError(f"{label} port out of range")
    _reject_tailscale_ip(host, label, allow_tailscale=allow_tailscale)
    return f"{host}:{port}"


def _reject_tailscale_ip(ip: str, label: str, *, allow_tailscale: bool) -> None:
    # Default: never use 100.x (Tailscale). Session opt-in: allow_tailscale.
    if allow_tailscale:
        return
    if ip.startswith("100."):
        raise ConfigError(f"{label} {ip} looks like Tailscale; not a bonded path")
