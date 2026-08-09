"""Pure X-drive inverse kinematics — no ROS or rhsp imports, unit-testable.

Wheel/motor order is [front_left, front_right, rear_left, rear_right], matching
REV Hub motor ports 0..3. Inputs use REP-103 body conventions:
  forward : +x, robot drives forward
  strafe  : +y, robot translates to its LEFT
  yaw     : +z, robot rotates counter-clockwise (viewed from above)

An X-drive (4 omni wheels at 45 degrees) uses the same mixing matrix as a
mecanum drive; the physical wheel/roller orientation is absorbed into per-wheel
sign flips, kept configurable so a backwards-wired motor is fixed in config
rather than in solder. Each axis also has a scale (which may be negative to
invert the whole axis).
"""
from dataclasses import dataclass


@dataclass
class WheelConfig:
    # Per-wheel sign [fl, fr, rl, rr] — flip a motor that spins the wrong way.
    signs: tuple = (1, 1, 1, 1)
    # Output scale in rhsp constant-power units (|power| <= 32767). START LOW.
    max_power: int = 8000
    # Per-axis gains; negative inverts that axis globally.
    forward_scale: float = 1.0
    strafe_scale: float = 1.0
    yaw_scale: float = 1.0


def mix(forward: float, strafe: float, yaw: float, cfg: WheelConfig) -> list:
    """Map (forward, strafe, yaw), each nominally in [-1, 1], to 4 motor powers.

    Returns integer powers [fl, fr, rl, rr] each in [-max_power, max_power].
    If any wheel would exceed full scale, all wheels are scaled down together so
    the commanded motion direction is preserved (no clipping distortion).
    """
    f = forward * cfg.forward_scale
    s = strafe * cfg.strafe_scale
    w = yaw * cfg.yaw_scale
    raw = [
        f + s + w,   # 0 front-left
        f - s - w,   # 1 front-right
        f - s + w,   # 2 rear-left
        f + s - w,   # 3 rear-right
    ]
    peak = max((abs(v) for v in raw), default=0.0)
    if peak > 1.0:                       # normalize so no wheel saturates
        raw = [v / peak for v in raw]
    return [int(round(sign * v * cfg.max_power))
            for sign, v in zip(cfg.signs, raw)]
