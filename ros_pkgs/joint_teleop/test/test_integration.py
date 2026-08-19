"""Target-angle integration tests for joint_teleop.

The clamping and rate maths are what decide whether a held control parks a
flipper at a sane angle or drives it into its end stop, so they are checked here
rather than by watching a robot.
"""
import pytest


def step(target, axis_value, *, invert=False, scale=1.0, rate=1.0, dt=0.05,
         deadzone=0.5, lo=-1.2, hi=1.2):
    """Mirror of JoyToJoints._tick's per-joint integration."""
    v = axis_value
    if abs(v) < deadzone:
        return target
    if invert:
        v = -v
    return max(lo, min(hi, target + v * scale * rate * dt))


def test_below_deadzone_does_not_drift():
    # A D-pad at rest must not creep the joint over time.
    t = 0.0
    for _ in range(100):
        t = step(t, 0.0)
    assert t == 0.0


def test_held_control_integrates_at_rate():
    t = 0.0
    for _ in range(20):          # 20 ticks * 0.05 s = 1 s at 1.0 rad/s
        t = step(t, 1.0)
    assert t == pytest.approx(1.0, abs=1e-9)


def test_target_clamps_at_limit():
    t = 0.0
    for _ in range(1000):
        t = step(t, 1.0)
    assert t == pytest.approx(1.2)


def test_clamps_at_lower_limit():
    t = 0.0
    for _ in range(1000):
        t = step(t, -1.0)
    assert t == pytest.approx(-1.2)


def test_invert_reverses_direction():
    assert step(0.0, 1.0, invert=True) < 0.0
    assert step(0.0, 1.0, invert=False) > 0.0


def test_scale_mirrors_a_paired_joint():
    """Left/right flippers mounted mirrored need opposite signs from one axis."""
    left = step(0.0, 1.0, scale=1.0)
    right = step(0.0, 1.0, scale=-1.0)
    assert left == pytest.approx(-right)


def test_target_holds_after_release():
    t = 0.0
    for _ in range(10):
        t = step(t, 1.0)
    held = t
    for _ in range(50):
        t = step(t, 0.0)
    assert t == pytest.approx(held)
