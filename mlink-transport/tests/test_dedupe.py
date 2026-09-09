from proto.dedupe import DedupeWindow


def test_duplicate_dropped() -> None:
    w = DedupeWindow(window=8)
    assert w.observe(1) == "deliver"
    assert w.observe(1) == "duplicate"


def test_gap_delivered_without_waiting() -> None:
    w = DedupeWindow(window=8)
    assert w.observe(1) == "deliver"
    assert w.observe(5) == "deliver"
    assert w.observe(2) == "deliver"


def test_late_after_window_dropped() -> None:
    w = DedupeWindow(window=8)
    assert w.observe(1) == "deliver"
    assert w.observe(10) == "deliver"
    # highest=10, window=8 → seq 2 is late (10-2 >= 8)
    assert w.observe(2) == "late"
    assert w.observe(3) == "deliver"
