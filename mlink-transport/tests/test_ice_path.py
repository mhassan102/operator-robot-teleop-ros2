"""T11 control: app datagrams on a nominated connection, no Tailscale."""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from pathlib import Path

import pytest
import yaml

from proto.config import ConfigError, MlinkConfig, PathConfig, load_config
from proto.ice_link import IceLink
from proto.session import MlinkSession

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def test_ice_yaml_is_localhost_control_only() -> None:
    op = load_config(ROOT / "config" / "lab-op-ice.yaml")
    edge = load_config(ROOT / "config" / "lab-edge-ice.yaml")
    assert op.listen_app == "127.0.0.1:5501"
    assert op.send_app == "127.0.0.1:5502"
    assert edge.listen_app == "127.0.0.1:5503"
    assert edge.send_app == "127.0.0.1:5504"
    assert op.listen_media == op.send_media == ""
    assert edge.listen_media == edge.send_media == ""
    assert op.allow_tailscale is False
    assert edge.allow_tailscale is False
    assert op.max_payload == 1400
    assert edge.down_timeout_us == 1_500_000
    for cfg, name in ((op, "local_op.yaml"), (edge, "local_edge.yaml")):
        path = cfg.paths[0]
        assert path.name == "ice"
        assert path.transport == "ice"
        assert path.ifname is None
        assert not path.bind_ip.startswith("100.")
        assert not path.peer_ip.startswith("100.")
        assert path.ice_config.endswith(f"turn/config/{name}")
        assert "tailscale" not in path.ice_config


def test_ice_path_rejects_udp_peer_and_tailscale() -> None:
    with pytest.raises(ConfigError, match="no UDP peer"):
        load_config(
            {
                "session_id": 1,
                "paths": [
                    {
                        "name": "ice",
                        "transport": "ice",
                        "ice_config": "local_op.yaml",
                        "peer": "3.227.234.95:50009",
                    }
                ],
            }
        )
    with pytest.raises(ConfigError, match="tailscale0"):
        load_config(
            {
                "session_id": 1,
                "paths": [
                    {
                        "name": "ice",
                        "transport": "ice",
                        "ifname": "tailscale0",
                        "ice_config": "local_op.yaml",
                    }
                ],
            }
        )
    with pytest.raises(ConfigError, match="100."):
        load_config(
            {
                "session_id": 1,
                "paths": [
                    {
                        "name": "ice",
                        "transport": "udp",
                        "bind_ip": "100.1.2.3",
                        "peer": "192.0.2.1:46000",
                    }
                ],
            }
        )


class _Pipe:
    def __init__(self, inbox: deque[bytes], outbox: deque[bytes]) -> None:
        self.inbox = inbox
        self.outbox = outbox

    def sendto(self, data: bytes, addr: tuple[str, int]) -> int:
        del addr
        self.outbox.append(data)
        return len(data)

    def recvfrom(self) -> tuple[bytes, tuple[str, int]] | None:
        if not self.inbox:
            return None
        return self.inbox.popleft(), ("203.0.113.8", 50009)

    def close(self) -> None:
        return None

    def fileno(self) -> int:
        return -1


class _OneSocket:
    def __init__(self, sock: _Pipe) -> None:
        self.sock = sock

    def create(self, path: PathConfig) -> _Pipe:
        assert path.transport == "ice"
        assert path.ifname != "tailscale0"
        assert not path.bind_ip.startswith("100.")
        assert not path.peer_ip.startswith("100.")
        return self.sock


def _ice_cfg(listen: str, send: str) -> MlinkConfig:
    return MlinkConfig(
        session_id=1,
        paths=(
            PathConfig(
                name="ice",
                bind_ip="0.0.0.0",
                peer_ip="0.0.0.0",
                peer_port=9,
                transport="ice",
                ice_config="/tmp/local.yaml",
            ),
        ),
        listen_app=listen,
        send_app=send,
    )


def test_app_datagrams_cross_nominated_stand_in(clock: object) -> None:
    """Both directions on a stand-in nominated connection. No tailscale0, no 100.x."""
    a_to_b: deque[bytes] = deque()
    b_to_a: deque[bytes] = deque()
    op = MlinkSession(
        _ice_cfg("127.0.0.1:5501", "127.0.0.1:5502"),
        clock,  # type: ignore[arg-type]
        _OneSocket(_Pipe(b_to_a, a_to_b)),
    )
    edge = MlinkSession(
        _ice_cfg("127.0.0.1:5503", "127.0.0.1:5504"),
        clock,  # type: ignore[arg-type]
        _OneSocket(_Pipe(a_to_b, b_to_a)),
    )
    op.send(b"grip-open")
    assert edge.poll() == [b"grip-open"]
    edge.send(b"grip-state")
    assert op.poll() == [b"grip-state"]
    assert op.path("ice").ifname != "tailscale0"
    assert not op.path("ice").bind_ip.startswith("100.")
    assert not edge.path("ice").peer_ip.startswith("100.")


def _ice_yaml(path: Path, *, role: str, url: str, room: str) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "role": role,
                "bind_ip": "127.0.0.1",
                "ifname": None,
                "stun_server": None,
                "stun_port": 3478,
                "turn_server": None,
                "ice_policy": "all",
                "signalling_url": url,
                "room": room,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_ice_link_loopback_both_directions(tmp_path: Path) -> None:
    """Real aioice nomination on 127.0.0.1, then bytes both ways. No STUN, no EC2."""
    import sys

    turn = REPO / "turn"
    sys.path.insert(0, str(turn))
    from signalling.server import listening_uri, start_server

    holder: dict[str, object] = {"ready": threading.Event()}

    def _serve() -> None:
        async def main() -> None:
            server = await start_server("127.0.0.1", 0)
            holder["url"] = listening_uri(server)
            stop = asyncio.Event()
            holder["stop"] = stop
            holder["loop"] = asyncio.get_running_loop()
            ready = holder["ready"]
            assert isinstance(ready, threading.Event)
            ready.set()
            await stop.wait()
            server.close()
            await server.wait_closed()

        asyncio.run(main())

    server_thread = threading.Thread(target=_serve, name="sig", daemon=True)
    server_thread.start()
    ready = holder["ready"]
    assert isinstance(ready, threading.Event)
    assert ready.wait(5), "signalling server did not start"

    room = f"mlink-ice-{time.time_ns()}"
    url = str(holder["url"])
    op_yaml = tmp_path / "local_op.yaml"
    edge_yaml = tmp_path / "local_edge.yaml"
    _ice_yaml(op_yaml, role="controlling", url=url, room=room)
    _ice_yaml(edge_yaml, role="controlled", url=url, room=room)
    op = IceLink(str(op_yaml), expect_role="controlling")
    edge = IceLink(str(edge_yaml), expect_role="controlled")
    errors: list[BaseException] = []

    def _start(link: IceLink) -> None:
        try:
            link.start()
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=_start, args=(op,), daemon=True),
        threading.Thread(target=_start, args=(edge,), daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(40)
    try:
        assert not errors, errors
        assert op.nominated.get("kind") == "direct"
        assert edge.nominated.get("kind") == "direct"
        assert "100." not in str(op.nominated.get("local", ""))
        assert "100." not in str(edge.nominated.get("remote", ""))

        def _wait(link: IceLink, timeout: float = 5.0) -> bytes:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                got = link.recvfrom()
                if got is not None:
                    return got[0]
                time.sleep(0.01)
            raise AssertionError("no datagram")

        assert op.sendto(b"op-to-edge", ("0.0.0.0", 9)) == len(b"op-to-edge")
        assert _wait(edge) == b"op-to-edge"
        assert edge.sendto(b"edge-to-op", ("198.51.100.1", 1)) == len(b"edge-to-op")
        assert _wait(op) == b"edge-to-op"
    finally:
        op.close()
        edge.close()
        loop = holder.get("loop")
        stop = holder.get("stop")
        if loop is not None and stop is not None:
            cast_loop = loop
            assert isinstance(cast_loop, asyncio.AbstractEventLoop)
            cast_loop.call_soon_threadsafe(stop.set)  # type: ignore[union-attr]
        server_thread.join(5)
