from teleop_demo.commands import make_twist
from teleop_demo.safety import (
    CONNECTED,
    DISPOSITION_ACCEPTED,
    DISPOSITION_CLAMPED,
    DISPOSITION_HELD,
    DISPOSITION_REJECTED,
    KEEP_ALIVE_HEARTBEAT_ONLY,
    RESTORED,
    SAFE_STOP,
    TIMEOUT,
    SafetyController,
)


def _controller(**overrides) -> SafetyController:
    values = {
        "watchdog_timeout_s": 0.5,
        "command_timeout_s": 0.2,
        "max_linear": 0.1,
        "max_angular": 0.3,
        "min_gripper": 0.0,
        "max_gripper": 1.0,
        "default_frame": "tool0",
    }
    values.update(overrides)
    return SafetyController(**values)


def test_startup_latches_safe_stop() -> None:
    safety = _controller()
    output = safety.tick(0.0)
    assert SAFE_STOP in output.transitions
    assert output.connection_state == TIMEOUT
    assert output.twist.linear.x == 0.0


def test_first_command_connects_and_clamps() -> None:
    safety = _controller()
    safety.tick(0.0)
    twist = make_twist(linear_x=1.5)
    disposition, transitions = safety.on_command(twist, 0.0, "tool0", 0.01)
    assert CONNECTED in transitions
    assert disposition == DISPOSITION_CLAMPED
    output = safety.tick(0.02)
    assert output.twist.linear.x == 0.1
    assert output.watchdog_state == "OK"


def test_nan_and_inf_are_rejected() -> None:
    safety = _controller()
    safety.tick(0.0)
    safety.on_command(make_twist(linear_x=0.05), 0.0, "tool0", 0.01)
    nan_disp, _ = safety.on_command(make_twist(linear_x=float("nan")), 0.0, "tool0", 0.02)
    inf_disp, _ = safety.on_command(make_twist(angular_z=float("inf")), 0.0, "tool0", 0.03)
    assert nan_disp == DISPOSITION_REJECTED
    assert inf_disp == DISPOSITION_REJECTED
    output = safety.tick(0.04)
    assert output.twist.linear.x == 0.05


def test_command_silence_zeros_jog_without_timeout() -> None:
    safety = _controller()
    safety.on_command(make_twist(linear_x=0.05), 0.0, "tool0", 0.0)
    output = safety.tick(0.25)
    assert output.twist.linear.x == 0.0
    assert output.connection_state == CONNECTED
    assert TIMEOUT not in output.transitions


def test_watchdog_timeout_zeros_and_does_not_replay() -> None:
    safety = _controller()
    safety.on_command(make_twist(linear_x=0.05), 0.0, "tool0", 0.0)
    output = safety.tick(0.6)
    assert TIMEOUT in output.transitions
    assert SAFE_STOP in output.transitions
    assert output.twist.linear.x == 0.0

    safety.on_heartbeat(0.7)
    restored = safety.tick(0.72)
    assert RESTORED in restored.transitions or safety.connection_state == RESTORED
    assert restored.twist.linear.x == 0.0

    safety.on_command(make_twist(linear_x=0.05), 0.0, "tool0", 0.8)
    moving = safety.tick(0.82)
    assert moving.connection_state == CONNECTED
    assert moving.twist.linear.x == 0.05


def test_heartbeat_only_holds_commands_after_timeout() -> None:
    safety = _controller(keep_alive=KEEP_ALIVE_HEARTBEAT_ONLY)
    safety.on_heartbeat(0.0)
    safety.on_command(make_twist(linear_x=0.05), 0.0, "tool0", 0.01)
    timed_out = safety.tick(0.6)
    assert TIMEOUT in timed_out.transitions
    disposition, _ = safety.on_command(make_twist(linear_x=0.05), 0.0, "tool0", 0.61)
    assert disposition == DISPOSITION_HELD
    assert safety.tick(0.62).twist.linear.x == 0.0


def test_accepted_in_limit_command() -> None:
    safety = _controller()
    disposition, _ = safety.on_command(make_twist(linear_x=0.05), 1.0, "base_link", 0.0)
    assert disposition == DISPOSITION_ACCEPTED
    output = safety.tick(0.01)
    assert output.gripper == 1.0
    assert output.frame_id == "base_link"
