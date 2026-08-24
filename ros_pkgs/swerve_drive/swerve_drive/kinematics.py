"""Swerve inverse kinematics: one body-frame Twist -> per-module (steer, drive).

Pure functions, no ROS imports, so the maths is testable without a graph (same
split as revhub.kinematics). The node in twist_to_swerve.py is transport only.

Frames and conventions:
  * Body frame: +x forward, +y left, +z up; angular.z positive = CCW (ROS REP-103).
  * A module is described by where its steer pivot sits in the body frame (m)
    and phi0: the direction the wheel ROLLS when its steer joint reads zero,
    as a body-frame angle. CAD exports freeze each wheel at an arbitrary
    heading, so phi0 is per-module data, not a symmetry you may assume.
  * Steer commands are UNWRAPPED joint angles for a continuous joint: each
    command picks, among all angles that give the desired rolling direction
    (heading + k*2pi, and heading+pi with the drive reversed), the one nearest
    the previously commanded angle. That caps every steering move at 90
    degrees — the flip optimisation every real swerve controller does — and it
    is why the caller must feed the previous command back in.
"""
import math
from typing import List, NamedTuple, Sequence, Tuple


class Module(NamedTuple):
    name: str
    x: float          # steer pivot in body frame, m
    y: float
    phi0: float       # wheel rolling heading at steer angle 0, body frame, rad


def wrap(angle: float) -> float:
    """Wrap to (-pi, pi]."""
    return -((-angle + math.pi) % (2.0 * math.pi) - math.pi)


def mix(vx: float, vy: float, wz: float,
        modules: Sequence[Module], wheel_radius: float,
        prev_steer: Sequence[float]) -> List[Tuple[float, float]]:
    """Map a body-frame Twist to [(steer_angle, wheel_rad_per_s), ...].

    prev_steer: the steer angle last commanded per module (zeros on startup).
    A module whose wheel speed is zero has no defined heading; it holds
    prev_steer so releasing the sticks never swings the modules.
    """
    out = []
    for i, m in enumerate(modules):
        # velocity of this module's ground contact for the commanded twist
        mvx = vx - wz * m.y
        mvy = vy + wz * m.x
        speed = math.hypot(mvx, mvy)
        prev = prev_steer[i]
        if speed < 1e-9:
            out.append((prev, 0.0))
            continue
        desired = math.atan2(mvy, mvx) - m.phi0
        ahead = prev + wrap(desired - prev)            # roll forwards
        flipped = prev + wrap(desired + math.pi - prev)  # roll backwards
        if abs(ahead - prev) <= abs(flipped - prev):
            out.append((ahead, speed / wheel_radius))
        else:
            out.append((flipped, -speed / wheel_radius))
    return out
