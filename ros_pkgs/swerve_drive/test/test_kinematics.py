"""Kinematics tests for swerve_drive.

Pure maths, no ROS graph. The PATRIBOTS fixture is the real module table from
the patribots model (urdf-collection, UPSTREAM.md) — the same numbers wired
into inventory/host_vars/buzzkill.yml — so a typo there has a failing twin
here. The gz-level validation on buzzkill (2026-08-23) measured this exact
geometry translating, strafing and rotating in place.
"""
import math

import pytest

from swerve_drive.kinematics import Module, mix, wrap

R = 0.0508   # patribots wheel radius, m

PATRIBOTS = [
    Module('front_left',   0.276,  0.277, math.radians(-52.87)),
    Module('front_right',  0.276, -0.276, math.radians(168.67)),
    Module('rear_left',   -0.276,  0.277, math.radians(39.89)),
    Module('rear_right',  -0.276, -0.276, math.radians(132.71)),
]

ZEROS = [0.0, 0.0, 0.0, 0.0]


def headings(twist, prev=ZEROS):
    """Rolling direction each module ends up pointing, body frame."""
    out = []
    for m, (steer, drive) in zip(PATRIBOTS, mix(*twist, PATRIBOTS, R, prev)):
        h = m.phi0 + steer + (0.0 if drive >= 0 else math.pi)
        out.append(wrap(h))
    return out


def test_forward_all_wheels_roll_forward():
    for h in headings((1.0, 0.0, 0.0)):
        assert h == pytest.approx(0.0, abs=1e-9)
    for _, drive in mix(1.0, 0.0, 0.0, PATRIBOTS, R, ZEROS):
        assert abs(drive) == pytest.approx(1.0 / R)


def test_strafe_left_all_wheels_roll_left():
    for h in headings((0.0, 1.0, 0.0)):
        assert h == pytest.approx(math.pi / 2, abs=1e-9)


def test_spin_ccw_wheels_tangential():
    # for pure rotation each wheel's velocity is perpendicular to its radius
    for m, h in zip(PATRIBOTS, headings((0.0, 0.0, 1.0))):
        radial = math.atan2(m.y, m.x)
        assert abs(wrap(h - radial)) == pytest.approx(math.pi / 2, abs=1e-9)
    # and speed = |r| * omega / wheel radius
    for m, (_, drive) in zip(PATRIBOTS, mix(0.0, 0.0, 1.0, PATRIBOTS, R, ZEROS)):
        assert abs(drive) == pytest.approx(math.hypot(m.x, m.y) / R)


def test_zero_twist_holds_steer_and_stops():
    prev = [0.3, -1.2, 2.0, 0.7]
    for p, (steer, drive) in zip(prev, mix(0.0, 0.0, 0.0, PATRIBOTS, R, prev)):
        assert steer == p
        assert drive == 0.0


def test_never_swings_more_than_quarter_turn():
    # scan of headings from many starting angles: the flip optimisation must
    # cap every steering move at 90 degrees
    for prev_angle in [x * 0.37 for x in range(-17, 18)]:
        prev = [prev_angle] * 4
        for ang in [x * 0.25 for x in range(25)]:
            cmds = mix(math.cos(ang), math.sin(ang), 0.0, PATRIBOTS, R, prev)
            for p, (steer, _) in zip(prev, cmds):
                assert abs(steer - p) <= math.pi / 2 + 1e-9


def test_reverse_flips_drive_not_steer():
    # +x then -x: the wheel should reverse, not the module swing pi
    first = mix(1.0, 0.0, 0.0, PATRIBOTS, R, ZEROS)
    prev = [s for s, _ in first]
    second = mix(-1.0, 0.0, 0.0, PATRIBOTS, R, prev)
    for (s1, d1), (s2, d2) in zip(first, second):
        assert s2 == pytest.approx(s1)
        assert d2 == pytest.approx(-d1)


def test_steer_targets_are_continuous_not_wrapped():
    # a module already wound to +3 rad must be commanded near +3, not near -pi
    prev = [3.0] * 4
    for p, (steer, _) in zip(prev, mix(1.0, 0.0, 0.0, PATRIBOTS, R, prev)):
        assert abs(steer - p) <= math.pi / 2 + 1e-9


def test_translation_plus_rotation_differs_per_module():
    cmds = mix(0.5, 0.0, 1.0, PATRIBOTS, R, ZEROS)
    speeds = sorted(abs(d) for _, d in cmds)
    # outer modules (relative to the turn) must spin faster than inner ones
    assert speeds[0] < speeds[-1]


def test_wrap_range():
    assert wrap(math.pi) == pytest.approx(math.pi)
    assert wrap(-math.pi) == pytest.approx(math.pi)
    assert wrap(3 * math.pi + 0.1) == pytest.approx(-math.pi + 0.1)
