"""Build the robot inventory from text fixtures or from sysfs.

``inventory_from`` does not open live ``/dev`` nodes. Tests pass fixture
text and a directory or mapping of by-id names. The window reads
``/sys/class/net``, ``/sys/class/video4linux/*/name``, and
``/dev/serial/by-id`` and feeds the same parser.

Fixture text (blank lines and ``#`` comments are ignored)::

    net <name> <operstate> <kind>
    video <node> <name words>

``kind`` is ``device``, ``loopback``, ``bridge``, ``veth``, or
``tailscale``. Link text::

    addr <name> <ipv4>
    route <name>

``route`` names the interface that owns the default route.
"""

from __future__ import annotations

import fcntl
import os
import socket
import struct
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

FOLLOWER_ID = "5B61033180"
LEADER_ID = "5B3E090040"
DEFAULT_VIDEO = "USB2.0_CAM1"

_HIDDEN_KINDS = frozenset({"loopback", "bridge", "veth", "tailscale"})
_SIOCGIFADDR = 0x8915


def serial_label(by_id_name: str) -> str:
    """Follower and leader are the two known adapters. Anything else is serial."""
    if FOLLOWER_ID in by_id_name:
        return "follower"
    if LEADER_ID in by_id_name:
        return "leader"
    return "serial"


def default_arm(arms: Sequence[Mapping[str, Any]]) -> str | None:
    """By-id path of the follower when that adapter is present."""
    for arm in arms:
        if arm.get("label") != "follower":
            continue
        path = arm.get("path")
        if isinstance(path, str) and path:
            return path
    return None


def default_video(videos: Sequence[Mapping[str, Any]]) -> str | None:
    """Capture node whose name contains ``USB2.0_CAM1``."""
    for video in videos:
        name = video.get("name")
        path = video.get("path")
        if (
            isinstance(name, str)
            and DEFAULT_VIDEO in name
            and isinstance(path, str)
            and path
        ):
            return path
    return None


def camera_page_for(ipv4: str | None) -> str:
    """MediaMTX page on the robot's Tailscale address, or ``\"\"``."""
    if not isinstance(ipv4, str):
        return ""
    text = ipv4.strip()
    if not text or not _is_ipv4(text):
        return ""
    return f"http://{text}:8889/cam/"


def _is_ipv4(text: str) -> bool:
    parts = text.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit() or (len(part) > 1 and part.startswith("0")):
            return False
        if int(part) > 255:
            return False
    return True


def hidden_interface(name: str, kind: str) -> bool:
    """Drop loopback, Tailscale, docker bridges, and veth from the inventory."""
    if kind in _HIDDEN_KINDS:
        return True
    if name == "lo" or name == "tailscale0" or name.startswith("tailscale"):
        return True
    if name.startswith("veth"):
        return True
    if name == "docker0" or name.startswith("docker") or name.startswith("br-"):
        return True
    return False


def interface_kind(name: str, iface: Path | None = None) -> str:
    if name == "lo":
        return "loopback"
    if name == "tailscale0" or name.startswith("tailscale"):
        return "tailscale"
    if name.startswith("veth"):
        return "veth"
    if name == "docker0" or name.startswith("docker") or name.startswith("br-"):
        return "bridge"
    if iface is not None and (iface / "bridge").is_dir():
        return "bridge"
    return "device"


def _video_path(node: str) -> str:
    if node.startswith("/"):
        return node
    return f"/dev/{node}"


def _is_metadata(name: str) -> bool:
    return "metadata" in name.lower()


def _parse_sysfs(sysfs_text: str) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    nets: list[tuple[str, str, str]] = []
    videos: list[tuple[str, str]] = []
    seen_net: set[str] = set()
    seen_video: set[str] = set()
    for raw in sysfs_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, _, rest = line.partition(" ")
        if head == "net":
            parts = rest.split()
            if len(parts) != 3:
                continue
            name, oper, kind = parts
            if not name or name in seen_net:
                continue
            seen_net.add(name)
            nets.append((name, oper, kind))
        elif head == "video":
            node, _, vname = rest.partition(" ")
            node = node.strip()
            if not node or node in seen_video:
                continue
            seen_video.add(node)
            videos.append((node, vname.strip()))
    return nets, videos


def _parse_links(links: str) -> tuple[dict[str, str], set[str]]:
    addresses: dict[str, str] = {}
    routes: set[str] = set()
    for raw in links.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) == 3 and parts[0] == "addr":
            name, ip = parts[1], parts[2]
            if name not in addresses and _is_ipv4(ip):
                addresses[name] = ip
        elif len(parts) == 2 and parts[0] == "route":
            routes.add(parts[1])
    return addresses, routes


def _by_id_path(name: str) -> str | None:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        return None
    return f"/dev/serial/by-id/{name}"


def _tty_path(target: str) -> str:
    target = target.strip()
    if not target:
        return ""
    if target.startswith("/"):
        return os.path.normpath(target)
    base = os.path.basename(target.rstrip("/"))
    if not base or base in {".", ".."}:
        return ""
    return f"/dev/{base}"


def _iter_serial(
    serial_dir: str | os.PathLike[str] | Mapping[str, str] | None,
) -> list[tuple[str, str]]:
    if serial_dir is None:
        return []
    if isinstance(serial_dir, Mapping):
        return [(str(name), str(target)) for name, target in serial_dir.items()]
    root = Path(serial_dir)
    if not root.is_dir():
        return []
    found: list[tuple[str, str]] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith(".") or not entry.is_symlink():
            continue
        try:
            target = os.readlink(entry)
        except OSError:
            continue
        found.append((entry.name, target))
    return found


def _arms_from(
    serial_dir: str | os.PathLike[str] | Mapping[str, str] | None,
) -> list[dict[str, str]]:
    arms: list[dict[str, str]] = []
    for name, target in _iter_serial(serial_dir):
        path = _by_id_path(name)
        if path is None:
            continue
        arms.append(
            {
                "path": path,
                "tty": _tty_path(target),
                "label": serial_label(name),
            }
        )
    return arms


def inventory_from(
    sysfs_text: str,
    links: str,
    serial_dir: str | os.PathLike[str] | Mapping[str, str] | None = None,
    camera_page: str = "",
) -> dict[str, Any]:
    """Return the protocol inventory object.

    ``camera_page`` is the string ``camera_page_for`` already produced.
    An empty string stays empty when Tailscale did not answer.
    """
    nets, videos = _parse_sysfs(sysfs_text)
    addresses, routes = _parse_links(links)
    interfaces: list[dict[str, Any]] = []
    for name, oper, kind in nets:
        if hidden_interface(name, kind):
            continue
        interfaces.append(
            {
                "name": name,
                "ipv4": addresses.get(name, ""),
                "up": oper.lower() == "up",
                "default_route": name in routes,
            }
        )
    listed_videos: list[dict[str, str]] = []
    for node, vname in videos:
        if _is_metadata(vname):
            continue
        listed_videos.append(
            {"path": _video_path(node), "name": vname, "kind": "capture"}
        )
    page = camera_page if isinstance(camera_page, str) else ""
    return {
        "v": 1,
        "type": "inventory",
        "interfaces": interfaces,
        "arms": _arms_from(serial_dir),
        "videos": listed_videos,
        "camera_page": page,
    }


def default_route_ifaces(route_text: str) -> list[str]:
    """Interface names whose ``/proc/net/route`` destination is ``00000000``."""
    names: list[str] = []
    for raw in route_text.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("iface"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "00000000" and parts[0] not in names:
            names.append(parts[0])
    return names


def format_links(addresses: Mapping[str, str], route_ifaces: Sequence[str]) -> str:
    lines = [f"addr {name} {ip}" for name, ip in addresses.items() if ip]
    lines.extend(f"route {name}" for name in route_ifaces)
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def collect_sysfs(
    net_root: str | Path = "/sys/class/net",
    video_root: str | Path = "/sys/class/video4linux",
) -> str:
    """Read sysfs and emit the text ``inventory_from`` parses."""
    lines: list[str] = []
    net = Path(net_root)
    if net.is_dir():
        for iface in sorted(net.iterdir(), key=lambda item: item.name):
            if not iface.is_dir():
                continue
            oper = _read_text(iface / "operstate") or "unknown"
            kind = interface_kind(iface.name, iface)
            lines.append(f"net {iface.name} {oper} {kind}")
    video = Path(video_root)
    if video.is_dir():
        for node in sorted(video.iterdir(), key=lambda item: item.name):
            if not node.is_dir():
                continue
            vname = " ".join(_read_text(node / "name").split())
            lines.append(f"video {node.name} {vname}".rstrip())
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def interface_ipv4(name: str) -> str:
    """IPv4 assigned to ``name``, or ``\"\"`` when the interface has none."""
    if not name or len(name) >= 16:
        return ""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        packed = fcntl.ioctl(
            sock.fileno(),
            _SIOCGIFADDR,
            struct.pack("256s", name.encode("utf-8")[:15]),
        )
    except OSError:
        return ""
    finally:
        sock.close()
    return socket.inet_ntoa(packed[20:24])


def collect_links(
    route_path: str | Path = "/proc/net/route",
    net_root: str | Path = "/sys/class/net",
    ipv4_of: Callable[[str], str] | None = None,
) -> str:
    """Address and default-route lines for ``inventory_from``."""
    root = Path(net_root)
    names = (
        sorted(entry.name for entry in root.iterdir() if entry.is_dir())
        if root.is_dir()
        else []
    )
    lookup = ipv4_of or interface_ipv4
    addresses: dict[str, str] = {}
    for name in names:
        try:
            ip = lookup(name)
        except OSError:
            ip = ""
        if ip and _is_ipv4(ip):
            addresses[name] = ip
    try:
        route_text = Path(route_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        route_text = ""
    return format_links(addresses, default_route_ifaces(route_text))


def _tailscale_ip4() -> str:
    proc = subprocess.run(
        ["tailscale", "ip", "-4"],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout


def read_tailscale_ipv4(runner: Callable[[], str] | None = None) -> str:
    """First IPv4 from ``tailscale ip -4``, or ``\"\"`` when that command fails."""
    if runner is None:
        runner = _tailscale_ip4
    try:
        text = runner()
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if not isinstance(text, str):
        return ""
    for line in text.splitlines():
        token = line.strip()
        if token:
            return token if _is_ipv4(token) else ""
    return ""


def live_inventory() -> dict[str, Any]:
    """Inventory for the window. Device paths come from this machine."""
    try:
        return inventory_from(
            collect_sysfs(),
            collect_links(),
            "/dev/serial/by-id",
            camera_page=camera_page_for(read_tailscale_ipv4()),
        )
    except Exception:
        return inventory_from("", "", None)


def format_inventory(inv: Mapping[str, Any]) -> str:
    """Text the robot terminal prints beside the arm."""
    arms = list(inv.get("arms") or [])
    videos = list(inv.get("videos") or [])
    chosen_arm = default_arm(arms)
    chosen_video = default_video(videos)
    lines = ["Interfaces"]
    interfaces = list(inv.get("interfaces") or [])
    if not interfaces:
        lines.append("  (none)")
    for item in interfaces:
        route = "default route" if item.get("default_route") else "local-only"
        state = "up" if item.get("up") else "down"
        ipv4 = item.get("ipv4") or "-"
        lines.append(f"  {item.get('name')}  {ipv4}  {state}  {route}")
    lines.append("")
    lines.append("Arms")
    if not arms:
        lines.append("  (none)")
    for arm in arms:
        mark = "  default" if arm.get("path") == chosen_arm else ""
        lines.append(
            f"  {arm.get('label')}  {arm.get('path')}  {arm.get('tty')}{mark}"
        )
    lines.append("")
    lines.append("Video")
    if not videos:
        lines.append("  (none)")
    for video in videos:
        mark = "  default" if video.get("path") == chosen_video else ""
        lines.append(f"  {video.get('path')}  {video.get('name')}{mark}")
    lines.append("")
    lines.append("Camera page")
    page = inv.get("camera_page") or "(none)"
    lines.append(f"  {page}")
    return "\n".join(lines)
