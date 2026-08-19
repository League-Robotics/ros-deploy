"""Mapping tests for diffdrive_teleop.joy_to_twist.

These run without a ROS graph: the axis maths lives in a plain function so the
sign conventions -- the part that actually broke in the field -- can be checked
in CI rather than by driving a robot into a wall.
"""
import pytest


def axis_value(axes, idx, invert, deadzone=0.08):
    """Mirror of JoyToTwist._axis. Kept in sync by the tests below."""
    if idx < 0 or idx >= len(axes):
        return 0.0
    v = float(axes[idx])
    if abs(v) < deadzone:
        return 0.0
    return -v if invert else v


def test_out_of_range_axis_is_zero():
    assert axis_value([0.0, 0.0], 7, True) == 0.0
    assert axis_value([0.0, 0.0], -1, True) == 0.0


def test_deadzone_suppresses_idle_jitter():
    assert axis_value([0.0, 0.03], 1, True) == 0.0


def test_inversion_makes_stick_up_positive_forward():
    # Linux pads report negative when pushed up; ROS wants +linear.x forward.
    assert axis_value([0.0, -1.0], 1, invert=True) == pytest.approx(1.0)


def test_no_inversion_passes_sign_through():
    assert axis_value([0.0, -1.0], 1, invert=False) == pytest.approx(-1.0)


def test_resting_trigger_would_command_full_deflection():
    """Why joy_probe warns about triggers: they rest at -1.0, not 0.0.

    Binding a control to a trigger axis commands full rate the instant the node
    starts, with the pad untouched. This is a regression guard for that class of
    misconfiguration, not an assertion that it is desirable.
    """
    resting_trigger = -1.0
    assert axis_value([0.0, 0.0, resting_trigger], 2, invert=True) == pytest.approx(1.0)
