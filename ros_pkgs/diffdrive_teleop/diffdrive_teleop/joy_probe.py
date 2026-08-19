"""joy_probe: report which axis a gamepad actually moves, and in which sign.

Axis indices and signs are NOT portable between gamepads, or even between modes
of the same gamepad (a Logitech F310 reports a different layout depending on the
X/D switch on its back). Guessing them produces controls that are subtly
inverted or, worse, a control bound to an analog TRIGGER — which rests at -1.0
and therefore commands full deflection the moment the node starts.

Run this, push one control at a time, and read off the index and sign to put in
`axis_drive` / `axis_turn` / `invert_*`:

    ros2 run diffdrive_teleop joy_probe --ros-args -p joy_topic:=/buzzkill/joy/joystick0

Output per event, e.g.:

    axis 1 -> -0.98   (push UP    => set axis_drive:=1  invert_drive:=true)

`invert` is true when pushing the intuitive positive direction (up / left)
produces a NEGATIVE reading, which is the usual Linux convention.
"""
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Joy


class JoyProbe(Node):
    def __init__(self):
        super().__init__('joy_probe')
        self.declare_parameter('joy_topic', '/joy')
        self.declare_parameter('threshold', 0.5)   # ignore idle jitter
        topic = self.get_parameter('joy_topic').value
        self.create_subscription(Joy, topic, self._on_joy, 10)
        self._resting = None
        self._last = {}
        self.get_logger().info(
            f'joy_probe: watching {topic}. Push ONE control at a time and hold it.')

    def _on_joy(self, msg: Joy):
        # The first frame is the resting state. Anything already at +/-1.0 there
        # is a trigger, not a stick, and must never be used as a drive/turn axis.
        if self._resting is None:
            self._resting = list(msg.axes)
            trig = [i for i, v in enumerate(self._resting) if abs(v) > 0.9]
            self.get_logger().info(f'{len(msg.axes)} axes, {len(msg.buttons)} buttons')
            if trig:
                self.get_logger().warn(
                    f'axes {trig} rest at full deflection -> these are TRIGGERS. '
                    f'Never assign drive/turn to them.')
            return

        thr = self.get_parameter('threshold').value
        for i, v in enumerate(msg.axes):
            if i >= len(self._resting):
                continue
            moved = v - self._resting[i]
            if abs(moved) < thr:
                continue
            # Only report on a meaningful change, so holding a stick does not spam.
            if abs(moved - self._last.get(i, 0.0)) < 0.25:
                continue
            self._last[i] = moved
            sign = 'NEGATIVE' if v < 0 else 'POSITIVE'
            self.get_logger().info(
                f'axis {i} -> {v:+.2f}  ({sign}; invert_* = '
                f'{"true" if v < 0 else "false"} if this is your UP/LEFT direction)')

        for i, b in enumerate(msg.buttons):
            if b:
                self.get_logger().info(f'button {i} pressed')


def main(args=None):
    rclpy.init(args=args)
    node = JoyProbe()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
