from teleop_demo.delivery import DeliveryTracker, LatencyStats, time_msg_to_ns


class _Stamp:
    def __init__(self, sec: int, nanosec: int) -> None:
        self.sec = sec
        self.nanosec = nanosec


def test_time_msg_to_ns() -> None:
    assert time_msg_to_ns(_Stamp(1, 250)) == 1_000_000_250


def test_in_order_delivery_has_zero_loss() -> None:
    tracker = DeliveryTracker()
    for sequence in range(1, 11):
        tracker.observe(sequence)
    assert tracker.received == 10
    assert tracker.unique == 10
    assert tracker.missing == 0
    assert tracker.duplicates == 0
    assert tracker.out_of_order == 0


def test_duplicate_and_reorder_counters() -> None:
    tracker = DeliveryTracker()
    for sequence in [1, 2, 2, 3, 4, 5]:
        tracker.observe(sequence)
    assert tracker.duplicates == 1
    assert tracker.missing == 0
    assert tracker.out_of_order == 0

    tracker.reset()
    for sequence in [1, 3, 2, 4, 5]:
        tracker.observe(sequence)
    assert tracker.out_of_order == 1
    assert tracker.missing == 0
    assert tracker.duplicates == 0
    assert tracker.unique == 5


def test_missing_sequence_gap() -> None:
    tracker = DeliveryTracker()
    for sequence in [1, 2, 4, 5]:
        tracker.observe(sequence)
    assert tracker.missing == 1
    assert tracker.unique == 4
    tracker.observe(3)
    assert tracker.missing == 0
    assert tracker.out_of_order == 1


def test_session_reset_clears_counters() -> None:
    tracker = DeliveryTracker()
    latency = LatencyStats()
    tracker.observe(1)
    tracker.observe(2)
    latency.add(1_000)
    tracker.reset()
    latency.reset()
    tracker.observe(1)
    latency.add(2_000)
    assert tracker.received == 1
    assert tracker.unique == 1
    assert latency.count == 1
    assert latency.average_ns == 2_000


def test_latency_statistics() -> None:
    stats = LatencyStats()
    stats.add(100)
    stats.add(300)
    stats.add(200)
    assert stats.current_ns == 200
    assert stats.min_ns == 100
    assert stats.max_ns == 300
    assert stats.average_ns == 200
    assert stats.count == 3
