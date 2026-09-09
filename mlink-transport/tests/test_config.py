from pathlib import Path

import pytest

from proto.config import ConfigError, load_config


def test_load_example_yaml() -> None:
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config" / "example.yaml")
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
