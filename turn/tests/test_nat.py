"""T6 NAT class and keepalive interval. No live STUN, no EC2, no idle sleep."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from pathlib import Path

import pytest
from aioice import stun
from aioice.ice import CONSENT_INTERVAL

from agent.ice import load_config
from agent.nat import (
    classify_mapping,
    consent_warning,
    format_keepalive_line,
    format_nat_line,
    is_coturn_port,
    is_stun_datagram,
    keepalive_interval,
    parse_holds,
    parse_keepalive_s,
    parse_mapping_ttl,
    print_keepalive,
    serve_binding,
    stun_endpoints,
    ttl_summary,
    wait_non_stun,
    binding_response,
    probe_mapping,
)
from agent.session import run_session

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config" / "example.yaml"

MAPPED = ("203.0.113.8", 40000)
DEST_A = ("192.0.2.1", 3478)
DEST_A_ALT = ("192.0.2.1", 3479)
DEST_B = ("198.51.100.1", 19302)


def test_consent_interval_is_five_seconds() -> None:
    assert int(CONSENT_INTERVAL) == 5


@pytest.mark.parametrize(
    ("mapped2", "dest2", "nat_class", "detail"),
    [
        (None, DEST_A_ALT, "untested", "untested"),
        (None, None, "untested", "untested"),
        (MAPPED, DEST_B, "endpoint-independent", "endpoint-independent"),
        (MAPPED, DEST_A_ALT, "endpoint-independent", "port-independent"),
        (("203.0.113.8", 40099), DEST_A_ALT, "endpoint-dependent", "address-and-port-dependent"),
        (("203.0.113.9", 40099), DEST_B, "endpoint-dependent", "address-dependent-unconfirmed"),
    ],
)
def test_classify_mapping(
    mapped2: tuple[str, int] | None,
    dest2: tuple[str, int] | None,
    nat_class: str,
    detail: str,
) -> None:
    assert classify_mapping(MAPPED, mapped2, DEST_A, dest2) == (nat_class, detail)


def test_classify_rejects_identical_destinations() -> None:
    with pytest.raises(ValueError, match="differ"):
        classify_mapping(MAPPED, MAPPED, DEST_A, DEST_A)


def test_format_nat_line_missing_second() -> None:
    from agent.nat import NatSample

    line = format_nat_line(NatSample("untested", "untested", MAPPED, None, DEST_A, DEST_A_ALT))
    assert line == (
        "nat class=untested detail=untested "
        "mapped1=203.0.113.8:40000 mapped2=missing "
        "dest1=192.0.2.1:3478 dest2=192.0.2.1:3479"
    )
    missing_dest = format_nat_line(NatSample("untested", "untested", MAPPED, None, DEST_A, None))
    assert missing_dest.endswith("dest2=missing")


def test_example_yaml_without_t6_keys_uses_default_interval() -> None:
    cfg = load_config(EXAMPLE)
    assert "keepalive_s" not in cfg
    assert "mapping_ttl_s" not in cfg
    assert "stun_server_2" not in cfg
    assert keepalive_interval(parse_keepalive_s(cfg), parse_mapping_ttl(cfg)) == (
        15,
        "yaml-default",
    )


def test_old_yaml_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "old.yaml"
    path.write_text(
        "role: controlling\nbind_ip: 192.0.2.10\nstun_server: 192.0.2.1\nstun_port: 3478\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert keepalive_interval(parse_keepalive_s(cfg), parse_mapping_ttl(cfg)) == (
        15,
        "yaml-default",
    )


def test_new_yaml_keys_load(tmp_path: Path) -> None:
    path = tmp_path / "t6.yaml"
    path.write_text(
        "\n".join(
            [
                "role: controlled",
                "bind_ip: 192.0.2.10",
                "stun_server: 192.0.2.1",
                "stun_port: 3478",
                "stun_server_2: 192.0.2.1",
                "stun_port_2: 3479",
                "keepalive_s: 15",
                "mapping_ttl_s: 40",
                "",
            ]
        ),
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert stun_endpoints(cfg) == (("192.0.2.1", 3478), ("192.0.2.1", 3479))
    assert keepalive_interval(parse_keepalive_s(cfg), parse_mapping_ttl(cfg)) == (
        15,
        "measured-ttl",
    )


def test_identical_stun_endpoints_rejected() -> None:
    cfg = {
        "stun_server": "192.0.2.1",
        "stun_port": 3478,
        "stun_server_2": "192.0.2.1",
        "stun_port_2": 3478,
    }
    with pytest.raises(ValueError, match="differ"):
        stun_endpoints(cfg)


def test_stun_server_2_tailscale_rejected() -> None:
    cfg = {
        "stun_server": "192.0.2.1",
        "stun_port": 3478,
        "stun_server_2": "100.64.0.1",
        "stun_port_2": 3479,
    }
    with pytest.raises(ValueError, match="100"):
        stun_endpoints(cfg)


def test_stun_server_2_requires_port() -> None:
    cfg = {"stun_server": "192.0.2.1", "stun_port": 3478, "stun_server_2": "192.0.2.2"}
    with pytest.raises(ValueError, match="stun_port_2"):
        stun_endpoints(cfg)


def test_unknown_ttl_returns_configured_interval() -> None:
    assert keepalive_interval(None, None) == (15, "yaml-default")
    assert keepalive_interval(7, None) == (7, "yaml-default")


def test_measured_ttl_uses_half_capped_by_configured() -> None:
    assert keepalive_interval(15, 40) == (15, "measured-ttl")
    assert keepalive_interval(15, 10) == (5, "measured-ttl")
    assert keepalive_interval(15, 2) == (1, "measured-ttl")
    assert keepalive_interval(30, 10) == (5, "measured-ttl")
    assert keepalive_interval(None, 100) == (15, "measured-ttl")


def test_measured_interval_is_below_ttl() -> None:
    for ttl in range(2, 120):
        for configured in (1, 15, 30, ttl):
            interval, basis = keepalive_interval(configured, ttl)
            assert basis == "measured-ttl"
            assert interval == min(configured, max(1, ttl // 2))
            assert 1 <= interval < ttl


def test_ttl_at_most_one_returns_one() -> None:
    with pytest.warns(UserWarning, match="1s"):
        assert keepalive_interval(15, 1) == (1, "measured-ttl")
    with pytest.warns(UserWarning, match="1s"):
        assert keepalive_interval(15, 0) == (1, "measured-ttl")


def test_consent_warning_when_ttl_at_most_five() -> None:
    assert consent_warning(None) is None
    assert consent_warning(6) is None
    assert consent_warning(5) is not None
    assert "not below" in (consent_warning(4) or "")
    line = format_keepalive_line("turn", 5, "measured-ttl", 10)
    assert line == (
        "keepalive path=turn interval_s=5 basis=measured-ttl ttl_s=10 consent_s=5"
    )


def test_print_keepalive_warns_on_short_ttl(capsys: pytest.CaptureFixture[str]) -> None:
    print_keepalive("direct", {"keepalive_s": 15, "mapping_ttl_s": 4})
    out, err = capsys.readouterr()
    assert out.strip() == (
        "keepalive path=direct interval_s=2 basis=measured-ttl ttl_s=4 consent_s=5"
    )
    assert "not below" in err
    assert "1s" not in err


def test_print_keepalive_unknown_ttl(capsys: pytest.CaptureFixture[str]) -> None:
    print_keepalive("direct", {})
    out, err = capsys.readouterr()
    assert out.strip() == (
        "keepalive path=direct interval_s=15 basis=yaml-default ttl_s=unknown consent_s=5"
    )
    assert err == ""


def test_ttl_summary_and_holds_parse() -> None:
    assert parse_holds("20,40,60") == [20, 40, 60]
    assert ttl_summary(40, [20, 40, 60]) == "nat_ttl changed_after=40"
    assert ttl_summary(None, [20, 40, 60]) == "nat_ttl inconclusive_within=60"
    with pytest.raises(ValueError):
        parse_holds("0,20")
    with pytest.raises(ValueError):
        parse_holds("nope")


def test_is_stun_datagram() -> None:
    raw = bytes(stun.Message(stun.Method.BINDING, stun.Class.REQUEST))
    assert is_stun_datagram(raw)
    assert not is_stun_datagram(b"nat-probe")
    assert not is_stun_datagram(b"")


def test_binding_response_roundtrip() -> None:
    req = stun.Message(stun.Method.BINDING, stun.Class.REQUEST)
    raw = binding_response(bytes(req), MAPPED)
    assert raw is not None
    msg = stun.parse_message(raw)
    assert msg.transaction_id == req.transaction_id
    assert msg.message_class == stun.Class.RESPONSE
    assert msg.attributes["XOR-MAPPED-ADDRESS"] == MAPPED
    assert binding_response(b"nat-probe", MAPPED) is None


def test_coturn_ports_rejected() -> None:
    assert is_coturn_port(3478)
    assert is_coturn_port(50000)
    assert is_coturn_port(50100)
    assert not is_coturn_port(3479)
    assert not is_coturn_port(50101)
    from agent.__main__ import main

    for port in (3478, 50000, 50100):
        assert main(["stun-respond", "--bind", "127.0.0.1", "--port", str(port)]) == 2
    assert main(["stun-respond", "--bind", "100.64.0.1", "--port", "3479"]) == 2


def test_wait_non_stun_ignores_binding() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        dest = sock.getsockname()

        def _send() -> None:
            time.sleep(0.02)
            sender.sendto(bytes(stun.Message(stun.Method.BINDING, stun.Class.REQUEST)), dest)
            sender.sendto(b"nat-probe", dest)

        threading.Thread(target=_send, daemon=True).start()
        assert wait_non_stun(sock, 2.0) is True
    finally:
        sender.close()
        sock.close()


def test_wait_non_stun_none_on_timeout() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    try:
        assert wait_non_stun(sock, 0.05) is False
    finally:
        sock.close()


class _ScriptedStun:
    """127.0.0.1 Binding responder that returns a fixed mapped address."""

    def __init__(self, mapped: tuple[str, int]) -> None:
        self.mapped = mapped
        self.sources: list[tuple[str, int]] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self.sock.close()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            self.sources.append(addr)
            reply = binding_response(data, self.mapped)
            if reply is None:
                continue
            try:
                self.sock.sendto(reply, addr)
            except OSError:
                return


def _write_cfg(tmp_path: Path, port: int, port2: int | None) -> Path:
    lines = [
        "role: controlling",
        "bind_ip: 127.0.0.1",
        "stun_server: 127.0.0.1",
        f"stun_port: {port}",
    ]
    if port2 is not None:
        lines.append("stun_server_2: 127.0.0.1")
        lines.append(f"stun_port_2: {port2}")
    path = tmp_path / "nat.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_cli_nat_two_local_responders(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Classifier on real UDP. Both replies are scripted; nothing leaves 127.0.0.1."""
    first = _ScriptedStun(MAPPED)
    second = _ScriptedStun(MAPPED)
    first.start()
    second.start()
    try:
        path = _write_cfg(tmp_path, first.port, second.port)
        from agent.__main__ import main

        assert main(["nat", "--config", str(path)]) == 0
        out = capsys.readouterr().out.strip()
        assert out == (
            "nat class=endpoint-independent detail=port-independent "
            f"mapped1=203.0.113.8:40000 mapped2=203.0.113.8:40000 "
            f"dest1=127.0.0.1:{first.port} dest2=127.0.0.1:{second.port}"
        )
        assert first.sources and second.sources
        assert first.sources[0][1] == second.sources[0][1]
        assert {src[0] for src in first.sources + second.sources} == {"127.0.0.1"}
    finally:
        first.close()
        second.close()


def test_silent_second_responder_is_untested() -> None:
    """Second socket accepts UDP and does not answer. No WAN, short timeout."""
    first = _ScriptedStun(MAPPED)
    first.start()
    silent = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    silent.bind(("127.0.0.1", 0))
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.bind(("127.0.0.1", 0))
    try:
        sample = probe_mapping(
            client,
            ("127.0.0.1", first.port),
            ("127.0.0.1", silent.getsockname()[1]),
            timeout=0.4,
        )
        assert (sample.nat_class, sample.detail) == ("untested", "untested")
        assert sample.mapped1 == MAPPED
        assert sample.mapped2 is None
        assert sample.dest2 == ("127.0.0.1", silent.getsockname()[1])
        assert first.sources and first.sources[0][0] == "127.0.0.1"
    finally:
        client.close()
        silent.close()
        first.close()


def test_cli_nat_missing_second_is_untested(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    only = _ScriptedStun(MAPPED)
    only.start()
    try:
        path = _write_cfg(tmp_path, only.port, None)
        from agent.__main__ import main

        assert main(["nat", "--config", str(path)]) == 0
        out = capsys.readouterr().out
        assert "class=untested" in out
        assert "detail=untested" in out
        assert "mapped2=missing" in out
        assert "dest2=missing" in out
    finally:
        only.close()


def test_listen_accepts_nat_probe_not_stun(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    only = _ScriptedStun(MAPPED)
    only.start()
    path = _write_cfg(tmp_path, only.port, None)
    code: dict[str, int] = {}

    def _run() -> None:
        from agent.nat import nat_check

        code["rc"] = nat_check(load_config(path), listen_s=2)

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 2
        while not only.sources and time.monotonic() < deadline:
            time.sleep(0.01)
        assert only.sources
        # Let the Binding transaction finish so this datagram is not consumed by it.
        time.sleep(0.2)
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sender.sendto(
                bytes(stun.Message(stun.Method.BINDING, stun.Class.REQUEST)),
                only.sources[0],
            )
            sender.sendto(b"nat-probe", only.sources[0])
        finally:
            sender.close()
        worker.join(timeout=3)
        assert not worker.is_alive()
        assert code["rc"] == 0
        out = capsys.readouterr().out
        assert "nat filtering=received" in out
        assert out.index("mapped1=") < out.index("nat filtering=received")
    finally:
        only.close()
        if worker.is_alive():
            worker.join(timeout=1)


def test_stun_respond_reflects_source() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    worker = threading.Thread(target=serve_binding, args=(srv,), daemon=True)
    worker.start()
    cli = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    cli.bind(("127.0.0.1", 0))
    try:
        req = stun.Message(stun.Method.BINDING, stun.Class.REQUEST)
        cli.sendto(bytes(req), srv.getsockname())
        cli.settimeout(2)
        data, _addr = cli.recvfrom(2048)
        msg = stun.parse_message(data)
        assert msg.attributes["XOR-MAPPED-ADDRESS"] == cli.getsockname()
    finally:
        cli.close()
        srv.close()
        worker.join(timeout=2)


def test_nat_ttl_cli_does_not_probe(tmp_path: Path) -> None:
    """Flag errors return before any hold sleep or STUN send."""
    path = _write_cfg(tmp_path, 9, None)
    from agent.__main__ import main

    assert main(["nat-ttl", "--config", str(path)]) == 2
    assert main(
        ["nat-ttl", "--config", str(path), "--holds", "20", "--refresh", "15", "--duration", "90"]
    ) == 2
    assert main(["nat-ttl", "--config", str(path), "--holds", "0"]) == 2
    assert main(["nat-ttl", "--config", str(path), "--refresh", "15"]) == 2


def test_run_prints_keepalive_after_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _Agent:
        def path(self) -> dict[str, str]:
            return {
                "kind": "turn",
                "local_type": "relay",
                "remote_type": "relay",
                "local": "192.0.2.1:50010",
                "remote": "192.0.2.1:50011",
            }

        async def close(self) -> None:
            return None

    async def fake_connect(cfg: dict) -> _Agent:
        del cfg
        print(
            "path=turn local=relay:192.0.2.1:50010 remote=relay:192.0.2.1:50011",
            flush=True,
        )
        return _Agent()

    async def fake_echo(agent: _Agent, role: str) -> bytes:
        del agent, role
        print("hello-ack", flush=True)
        return b"hello-ack"

    monkeypatch.setattr("agent.session.connect_agent", fake_connect)
    monkeypatch.setattr("agent.session._echo", fake_echo)
    cfg = {
        "role": "controlling",
        "bind_ip": "192.0.2.10",
        "stun_server": "192.0.2.1",
        "stun_port": 3478,
        "signalling_url": "ws://127.0.0.1:9",
        "room": "hello-world",
        "keepalive_s": 15,
        "mapping_ttl_s": 10,
    }
    result = asyncio.run(run_session(cfg, hold=False))
    assert result["ok"] is True
    assert result["path"] == "turn"
    out = capsys.readouterr().out
    path_at = out.index("path=turn")
    keep_at = out.index(
        "keepalive path=turn interval_s=5 basis=measured-ttl ttl_s=10 consent_s=5"
    )
    ack_at = out.index("hello-ack")
    assert path_at < keep_at < ack_at
