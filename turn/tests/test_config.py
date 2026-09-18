from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config" / "example.yaml"

REQUIRED_KEYS = (
    "role",
    "bind_ip",
    "stun_server",
    "stun_port",
    "turn_server",
    "turn_port",
    "turn_user",
    "turn_password",
    "signalling_url",
    "room",
)


def test_example_yaml_has_required_keys() -> None:
    with EXAMPLE.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    assert isinstance(cfg, dict)
    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    assert missing == [], f"missing keys: {missing}"
    assert cfg["role"] in ("controlling", "controlled")
