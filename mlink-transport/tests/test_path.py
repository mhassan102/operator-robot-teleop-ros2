import pytest

from proto.config import PathConfig
from proto.path import Path
from proto.scheduler import eligible_paths


def _path(name: str = "eth", path_id: int = 0, window: int = 20) -> Path:
    cfg = PathConfig(
        name=name,
        bind_ip="127.0.0.1",
        peer_ip="127.0.0.1",
        peer_port=1,
    )
    return Path(path_id, cfg, socket=None, now_us=0, loss_window=window)


def _fill_loss(path: Path, rate: float) -> None:
    n = path._loss_window
    drops: set[int] = set()
    n_drop = round(rate * n)
    if n_drop:
        stride = n / n_drop
        for k in range(n_drop):
            drops.add(int(k * stride) % n)
    path.hb_outcomes.clear()
    for i in range(n):
        path.hb_outcomes.append(i not in drops)


def test_loss_zero_until_window_full() -> None:
    p = _path()
    p.observe_heartbeat(1)
    assert p.loss() == 0.0


def test_heartbeat_gap_loss_is_thirty_percent() -> None:
    p = _path(window=10)
    received = [s for s in range(1, 11) if s % 3 != 0]
    for seq in received:
        p.observe_heartbeat(seq)
    assert len(p.hb_outcomes) == 10
    assert p.loss() == pytest.approx(0.30, abs=0.001)


def test_rtt_smoothed() -> None:
    p = _path()
    p.observe_rtt(1000)
    assert p.rtt_us == 1000
    p.observe_rtt(0)
    assert p.rtt_us == pytest.approx(800.0)


def test_scheduler_excludes_lossy_and_down() -> None:
    eth = _path("eth", 0)
    wifi = _path("wifi", 1)
    lte = _path("lte", 2)
    _fill_loss(eth, 0.30)
    _fill_loss(wifi, 0.0)
    _fill_loss(lte, 0.0)
    lte.up = False
    chosen = eligible_paths([eth, wifi, lte], 0.20)
    assert [p.name for p in chosen] == ["wifi"]
