"""joy_to_twist: gamepad (sensor_msgs/Joy) -> geometry_msgs/Twist for a
DIFFERENTIAL-drive robot.

A differential drive has exactly two degrees of freedom: drive (linear.x) and
turn (angular.z). It physically cannot move sideways, so this node never
populates linear.y — there is no strafe axis, no strafe parameter, and no way to
switch one on. That is the whole reason this package exists separately from the
holonomic X-drive teleop in `revhub`, rather than the diff-drive case being
bolted onto it.

Default layout is single-stick "arcade" drive on the left stick: push it forward
to drive, push it sideways to turn, push it diagonally to do both.

Every axis index and sign is a parameter, because they are NOT portable between
gamepads — the same Logitech F310 reports a different layout depending on the
X/D switch on its back. Use `ros2 run diffdrive_teleop joy_probe` to read the
actual indices and signs off a pad instead of guessing them.
"""
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Joy
from geometry_msgs.msg import Twist


class JoyToTwist(Node):
    def __init__(self):
        super().__init__('joy_to_twist')
        self.declare_parameter('joy_topic', '/joy')
        self.declare_parameter('cmd_vel_topic', '/cmd_vel')

        # Single-stick arcade defaults: left stick Y drives, left stick X turns.
        self.declare_parameter('axis_drive', 1)
        self.declare_parameter('axis_turn', 0)

        # Linux joysticks report negative when a stick is pushed up or left,
        # while ROS wants +linear.x forward and +angular.z counter-clockwise
        # (left). So both default to inverted. Pads vary — verify with joy_probe
        # rather than assuming, because getting these wrong inverts the controls
        # in a way that is easy to misread as a broken robot.
        self.declare_parameter('invert_drive', True)
        self.declare_parameter('invert_turn', True)

        self.declare_parameter('max_linear', 1.0)     # m/s at full deflection
        self.declare_parameter('max_angular', 1.0)    # rad/s at full deflection
        self.declare_parameter('deadzone', 0.08)

        # Dead-man button; -1 disables the requirement. When set, output is
        # produced only while that button is held.
        self.declare_parameter('enable_button', -1)

        joy_topic = self.get_parameter('joy_topic').value
        cmd_topic = self.get_parameter('cmd_vel_topic').value
        self._pub = self.create_publisher(Twist, cmd_topic, 10)
        self.create_subscription(Joy, joy_topic, self._on_joy, 10)

        # A centred pad still autorepeats Joy at ~20 Hz. Forwarding those would
        # stream zero Twists forever and stomp any other publisher on cmd_vel
        # (a navigation stack, a test script). Instead: publish while the sticks
        # are deflected, publish exactly ONE zero on release so the robot stops
        # promptly, then fall silent and leave the topic to whoever else wants it.
        self._was_active = False
        self.get_logger().info(f'joy_to_twist (differential): {joy_topic} -> {cmd_topic}')

    def _axis(self, msg, idx, invert):
        """Read one axis, apply deadzone and sign. Out-of-range index => 0.0."""
        if idx < 0 or idx >= len(msg.axes):
            return 0.0
        v = float(msg.axes[idx])
        if abs(v) < self.get_parameter('deadzone').value:
            return 0.0
        return -v if invert else v

    def _on_joy(self, msg: Joy):
        eb = self.get_parameter('enable_button').value
        enabled = eb < 0 or (eb < len(msg.buttons) and msg.buttons[eb] == 1)

        t = Twist()   # linear.y stays 0.0 — a differential drive cannot strafe.
        if enabled:
            t.linear.x = self._axis(msg, self.get_parameter('axis_drive').value,
                                    self.get_parameter('invert_drive').value) \
                * self.get_parameter('max_linear').value
            t.angular.z = self._axis(msg, self.get_parameter('axis_turn').value,
                                     self.get_parameter('invert_turn').value) \
                * self.get_parameter('max_angular').value

        active = t.linear.x != 0.0 or t.angular.z != 0.0
        if active:
            self._pub.publish(t)
            self._was_active = True
        elif self._was_active:
            self._pub.publish(t)      # one zero Twist: stop, then yield the topic
            self._was_active = False
            self.get_logger().info('sticks released — stopped, yielding cmd_vel')


def main(args=None):
    rclpy.init(args=args)
    node = JoyToTwist()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
