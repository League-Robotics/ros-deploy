"""Unit tests for the pure X-drive mixing (no ROS/rhsp needed: `pytest`)."""
from revhub.kinematics import WheelConfig, mix

CFG = WheelConfig(max_power=8000)


def test_stationary():
    assert mix(0.0, 0.0, 0.0, CFG) == [0, 0, 0, 0]


def test_pure_forward_all_wheels_equal():
    # forward -> every wheel drives the same way.
    assert mix(1.0, 0.0, 0.0, CFG) == [8000, 8000, 8000, 8000]


def test_pure_strafe_diagonal_pairs_oppose():
    # strafe-left: fl/rr one way, fr/rl the other.
    assert mix(0.0, 1.0, 0.0, CFG) == [8000, -8000, -8000, 8000]


def test_pure_yaw_left_right_oppose():
    # yaw-CCW: left wheels one way, right wheels the other.
    assert mix(0.0, 0.0, 1.0, CFG) == [8000, -8000, 8000, -8000]


def test_normalization_preserves_direction():
    # forward + strafe would peak at 2.0; all wheels scale down together.
    assert mix(1.0, 1.0, 0.0, CFG) == [8000, 0, 0, 8000]


def test_wheel_sign_flip():
    cfg = WheelConfig(signs=(-1, 1, 1, 1), max_power=8000)
    assert mix(1.0, 0.0, 0.0, cfg) == [-8000, 8000, 8000, 8000]
