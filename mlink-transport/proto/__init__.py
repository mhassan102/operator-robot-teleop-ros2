"""mlink v1 protocol library (Stage 1: no real NICs, no CLI)."""

from proto.clock import Clock, FakeClock, SystemClock
from proto.config import MlinkConfig, PathConfig, load_config
from proto.dedupe import DedupeWindow
from proto.header import (
    FLAG_ECHO,
    FLAG_HEARTBEAT,
    FLAG_PROBE,
    HEADER_SIZE,
    MAGIC,
    MAX_PAYLOAD,
    TC_CONTROL,
    TC_MEDIA,
    VERSION,
    HeaderError,
    Packet,
    PayloadTooLarge,
    decode,
    encode,
)
from proto.path import Path
from proto.scheduler import eligible_paths
from proto.session import MlinkSession
from proto.sockets import (
    FakeNetwork,
    FakeSocketFactory,
    UdpSocketFactory,
    drop_heartbeats,
    spaced_drop,
)

__all__ = [
    "FLAG_ECHO",
    "FLAG_HEARTBEAT",
    "FLAG_PROBE",
    "HEADER_SIZE",
    "MAGIC",
    "MAX_PAYLOAD",
    "TC_CONTROL",
    "TC_MEDIA",
    "VERSION",
    "Clock",
    "DedupeWindow",
    "FakeClock",
    "FakeNetwork",
    "FakeSocketFactory",
    "HeaderError",
    "MlinkConfig",
    "MlinkSession",
    "Packet",
    "Path",
    "PathConfig",
    "PayloadTooLarge",
    "SystemClock",
    "UdpSocketFactory",
    "decode",
    "drop_heartbeats",
    "eligible_paths",
    "encode",
    "load_config",
    "spaced_drop",
]
