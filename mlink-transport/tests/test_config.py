from pathlib import Path

import pytest
import yaml

from proto.config import ConfigError, load_config

ROOT = Path(__file__).resolve().parents[1]


def test_load_example_yaml() -> None:
    cfg = load_config(ROOT / "config" / "example.yaml")
    assert cfg.session_id == 1
    assert [p.name for p in cfg.paths] == ["eth", "wifi"]
    assert cfg.paths[0].peer_ip == "192.168.10.2"
    assert cfg.paths[0].peer_port == 46000
    assert cfg.paths[0].ifname == "enx00e04c681cc3"


def test_rejects_tailscale_ifname() -> None:
    with pytest.raises(ConfigError, match="tailscale0"):
        load_config(
            {
                "session_id": 1,
                "paths": [
                    {
                        "name": "ts",
                        "ifname": "tailscale0",
                        "bind_ip": "10.0.0.1",
                        "peer": "10.0.0.2:46000",
                    }
                ],
            }
        )


def test_rejects_100_x_peer() -> None:
    with pytest.raises(ConfigError, match="Tailscale"):
        load_config(
            {
                "session_id": 1,
                "paths": [
                    {
                        "name": "eth",
                        "bind_ip": "192.168.10.1",
                        "peer": "100.101.94.5:46000",
                    }
                ],
            }
        )


def test_loopback_yaml_pair_is_complementary() -> None:
    op = load_config(ROOT / "config" / "loopback.yaml")
    edge = load_config(ROOT / "config" / "loopback-edge.yaml")
    assert op.session_id == edge.session_id == 1
    app_ports = {op.listen_app, op.send_app, edge.listen_app, edge.send_app}
    assert app_ports == {
        "127.0.0.1:5501",
        "127.0.0.1:5502",
        "127.0.0.1:5503",
        "127.0.0.1:5504",
    }
    assert [p.name for p in op.paths] == ["eth", "wifi"]
    assert [p.name for p in edge.paths] == ["eth", "wifi"]
    assert op.paths[0].bind_port == 41001
    assert op.paths[0].peer_port == 42001
    assert edge.paths[0].bind_port == 42001
    assert edge.paths[0].peer_port == 41001
    assert op.paths[1].bind_port == 41002
    assert edge.paths[1].bind_port == 42002
    assert all(p.ifname is None for p in op.paths)
    assert all(p.ifname is None for p in edge.paths)


def test_optional_media_app_ports() -> None:
    cfg = load_config(
        {
            "session_id": 1,
            "listen_app": "127.0.0.1:5501",
            "send_app": "127.0.0.1:5502",
            "listen_media": "127.0.0.1:5601",
            "send_media": "127.0.0.1:5602",
            "paths": [
                {"name": "eth", "bind_ip": "127.0.0.1", "peer": "127.0.0.1:1"},
            ],
        }
    )
    assert cfg.listen_media == "127.0.0.1:5601"
    assert cfg.send_media == "127.0.0.1:5602"


def test_rejects_duplicate_app_addrs() -> None:
    with pytest.raises(ConfigError, match="unique"):
        load_config(
            {
                "session_id": 1,
                "listen_app": "127.0.0.1:5501",
                "send_app": "127.0.0.1:5501",
                "paths": [
                    {"name": "eth", "bind_ip": "127.0.0.1", "peer": "127.0.0.1:1"},
                ],
            }
        )


def test_rejects_100_x_media_addr() -> None:
    with pytest.raises(ConfigError, match="Tailscale"):
        load_config(
            {
                "session_id": 1,
                "listen_media": "100.95.150.54:5004",
                "paths": [
                    {"name": "eth", "bind_ip": "127.0.0.1", "peer": "127.0.0.1:1"},
                ],
            }
        )


def test_allows_tailscale_ifname_and_100_x_when_opted_in() -> None:
    cfg = load_config(
        {
            "session_id": 1,
            "allow_tailscale": True,
            "paths": [
                {
                    "name": "ts",
                    "ifname": "tailscale0",
                    "bind_ip": "100.95.150.54",
                    "bind_port": 46000,
                    "peer": "100.67.47.79:46000",
                }
            ],
        }
    )
    assert cfg.allow_tailscale is True
    assert cfg.paths[0].ifname == "tailscale0"
    assert cfg.paths[0].bind_ip == "100.95.150.54"
    assert cfg.paths[0].peer_ip == "100.67.47.79"
    assert cfg.paths[0].bind_port == 46000
    assert cfg.paths[0].peer_port == 46000


def test_allow_tailscale_false_still_rejects_100_x() -> None:
    with pytest.raises(ConfigError, match="Tailscale"):
        load_config(
            {
                "session_id": 1,
                "allow_tailscale": False,
                "paths": [
                    {
                        "name": "eth",
                        "bind_ip": "192.168.10.1",
                        "peer": "100.101.94.5:46000",
                    }
                ],
            }
        )


def test_allow_tailscale_false_still_rejects_tailscale0() -> None:
    with pytest.raises(ConfigError, match="tailscale0"):
        load_config(
            {
                "session_id": 1,
                "allow_tailscale": False,
                "paths": [
                    {
                        "name": "ts",
                        "ifname": "tailscale0",
                        "bind_ip": "10.0.0.1",
                        "peer": "10.0.0.2:46000",
                    }
                ],
            }
        )


def test_allow_tailscale_must_be_boolean() -> None:
    with pytest.raises(ConfigError, match="allow_tailscale"):
        load_config(
            {
                "session_id": 1,
                "allow_tailscale": "true",
                "paths": [
                    {"name": "eth", "bind_ip": "127.0.0.1", "peer": "127.0.0.1:1"},
                ],
            }
        )


def test_remote_laptop_yaml_loads_with_opt_in() -> None:
    op = load_config(ROOT / "config" / "lab-op-remote-laptop.yaml")
    edge = load_config(ROOT / "config" / "lab-edge-remote-laptop.yaml")
    assert op.allow_tailscale is True
    assert edge.allow_tailscale is True
    assert op.listen_media == ""
    assert op.send_media == ""
    assert edge.listen_media == ""
    assert edge.send_media == ""


def test_remote_laptop_yaml_rejects_without_opt_in() -> None:
    for name in ("lab-op-remote-laptop.yaml", "lab-edge-remote-laptop.yaml"):
        raw = yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))
        raw.pop("allow_tailscale", None)
        with pytest.raises(ConfigError, match="tailscale0|Tailscale"):
            load_config(raw)


def test_orin_lab_yaml_loads_without_tailscale_opt_in() -> None:
    op = load_config(ROOT / "config" / "lab-op.yaml")
    edge = load_config(ROOT / "config" / "lab-edge.yaml")
    assert op.allow_tailscale is False
    assert edge.allow_tailscale is False
    assert not any(p.ifname == "tailscale0" for p in (*op.paths, *edge.paths))
    assert not any(
        addr.startswith("100.")
        for p in (*op.paths, *edge.paths)
        for addr in (p.bind_ip, p.peer_ip)
    )


def test_third_path_is_config_only() -> None:
    cfg = load_config(
        {
            "session_id": 1,
            "paths": [
                {"name": "eth", "bind_ip": "127.0.0.1", "peer": "127.0.0.1:1"},
                {"name": "wifi", "bind_ip": "127.0.0.1", "peer": "127.0.0.1:2"},
                {"name": "lte", "bind_ip": "127.0.0.1", "peer": "127.0.0.1:3"},
            ],
        }
    )
    assert len(cfg.paths) == 3
    assert cfg.paths[2].name == "lte"
