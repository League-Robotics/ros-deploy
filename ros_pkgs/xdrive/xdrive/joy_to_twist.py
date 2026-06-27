"""joy_to_twist: map a gamepad (sensor_msgs/Joy) to a geometry_msgs/Twist.

Left stick  -> translation: forward/back (linear.x) + strafe (linear.y)
Right stick -> yaw (angular.z)

Axis indices and inversions default to a Logitech Dual Action (DirectInput:
LX=0, LY=1, RX=2, RY=3) but are all parameters, so any pad can be retuned via
`--ros-args -p ...` without rebuilding. An optional dead-man button gates output.
"""
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Joy
from geometry_msgs.msg import Twist


class JoyToTwist(Node):
    def __init__(self):
        super().__init__('joy_to_twist')
        self.declare_parameter('joy_topic', '/golem/joy/joystick0')
        self.declare_parameter('cmd_vel_topic', '/cmd_vel')
        self.declare_parameter('deadzone', 0.08)
        self.declare_parameter('max_linear', 1.0)
        self.declare_parameter('max_angular', 1.0)
        # Logitech Dual Action defaults (DirectInput): LX=0 LY=1 RX=2 RY=3.
        self.declare_parameter('axis_forward', 1)
        self.declare_parameter('axis_strafe', 0)
        self.declare_parameter('axis_yaw', 2)
        # Sticks read negative up/left; invert so up=+forward, left=+strafe/+yaw.
        self.declare_parameter('invert_forward', True)
        self.declare_parameter('invert_strafe', True)
        self.declare_parameter('invert_yaw', True)
        self.declare_parameter('enable_button', -1)   # -1 = no dead-man required

        joy_topic = self.get_parameter('joy_topic').value
        cmd_topic = self.get_parameter('cmd_vel_topic').value
        self._pub = self.create_publisher(Twist, cmd_topic, 10)
        self.create_subscription(Joy, joy_topic, self._on_joy, 10)
        self.get_logger().info(f'joy_to_twist: {joy_topic} -> {cmd_topic}')

    def _axis(self, msg, idx, invert):
        if idx < 0 or idx >= len(msg.axes):
            return 0.0
        v = float(msg.axes[idx])
        if abs(v) < self.get_parameter('deadzone').value:
            return 0.0
        return -v if invert else v

    def _on_joy(self, msg: Joy):
        eb = self.get_parameter('enable_button').value
        enabled = eb < 0 or (eb < len(msg.buttons) and msg.buttons[eb] == 1)
        t = Twist()
        if enabled:
            lin = self.get_parameter('max_linear').value
            ang = self.get_parameter('max_angular').value
            t.linear.x = self._axis(msg, self.get_parameter('axis_forward').value,
                                     self.get_parameter('invert_forward').value) * lin
            t.linear.y = self._axis(msg, self.get_parameter('axis_strafe').value,
                                     self.get_parameter('invert_strafe').value) * lin
            t.angular.z = self._axis(msg, self.get_parameter('axis_yaw').value,
                                     self.get_parameter('invert_yaw').value) * ang
        self._pub.publish(t)


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
