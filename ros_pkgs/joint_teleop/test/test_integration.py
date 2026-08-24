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


def test_per_joint_limits_differ():
    """A turret sweeping [0, 6.14] and a hood moving [-0.04, 0.4] share one
    node; each must clamp at ITS limit, not a global one."""
    turret, hood = 0.0, 0.0
    for _ in range(3000):
        turret = step(turret, 1.0, lo=0.0, hi=6.14)
        hood = step(hood, 1.0, lo=-0.0386, hi=0.3977)
    assert turret == pytest.approx(6.14)
    assert hood == pytest.approx(0.3977)


def test_one_sided_range_cannot_go_positive():
    # The intake's range is [-2.14, 0]: stowed IS the upper limit.
    t = 0.0
    for _ in range(100):
        t = step(t, 1.0, lo=-2.14, hi=0.0)
    assert t == 0.0


def velocity_step(running, held, *, speed):
    """Mirror of JoyToJoints._tick's velocity-joint branch: returns
    (published_value_or_None, running)."""
    if held:
        return speed, True
    if running:
        return 0.0, False
    return None, False


def test_velocity_joint_publishes_speed_while_held():
    out, running = velocity_step(False, True, speed=200.0)
    assert out == 200.0 and running


def test_velocity_joint_stops_once_then_goes_quiet():
    _, running = velocity_step(False, True, speed=200.0)
    out, running = velocity_step(running, False, speed=200.0)
    assert out == 0.0 and not running          # the single stop message
    out, running = velocity_step(running, False, speed=200.0)
    assert out is None                          # then the topic is left alone


def test_velocity_joint_idle_never_publishes():
    out, running = velocity_step(False, False, speed=200.0)
    assert out is None and not running
