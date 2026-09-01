"""QoS for command and acknowledgement topics.

Small KEEP_LAST history avoids a long backlog of stale commands. RELIABLE is
used so an unimpaired local network has a zero-loss baseline.
"""

from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy


def command_qos() -> QoSProfile:
    return QoSProfile(
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.VOLATILE,
        history=HistoryPolicy.KEEP_LAST,
        depth=10,
    )
