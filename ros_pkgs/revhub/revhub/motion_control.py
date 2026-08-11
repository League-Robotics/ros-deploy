"""motion_control: arbitrate command sources onto the robot's cmd_vel.

Inputs:
  * A sensor_msgs/Joy topic (the gamepad publisher, normally on the same host).
  * A geometry_msgs/Twist topic from a motion planner (optional, may be silent).

Output: geometry_msgs/Twist motion commands on cmd_vel — linear.x forward,
linear.y left, angular.z CCW, each nominally in [-1, 1] — consumed by
revhub_node (joy-source=twist), which applies kinematics and the hub's
per-wheel velocity PID.

Arbitration: the joystick always wins. While any stick is outside its deadzone
the pad's mapped Twist is published and planner messages are DISCARDED; on
stick release ONE zero Twist is sent (prompt stop) and, after a short guard
window, planner messages pass through verbatim. Both silent -> nothing is
published and revhub_node's command timeout holds the motors at zero.

Axis mapping defaults match the 8BitDo arcade layout calibrated on baldur
2026-08-04 (left stick Y=forward / X=rotate, right stick X=strafe); all
indices/inversions are parameters, so any pad can be retuned via --ros-args.
"""
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Joy
from geometry_msgs.msg import Twist


class MotionControl(Node):
    def __init__(self):
        super().__init__('motion_control')
        self.declare_parameter('joy_topic', '/baldur/joy/joystick0')
        self.declare_parameter('planner_topic', 'planner/cmd_vel')
        self.declare_parameter('cmd_vel_topic', '/cmd_vel')
        self.declare_parameter('deadzone', 0.08)
        self.declare_parameter('max_linear', 1.0)
        self.declare_parameter('max_angular', 1.0)
        # 8BitDo arcade layout (baldur 2026-08-04): LY=1 fwd (inverted),
        # RX=3 strafe, LX=0 yaw (both non-inverted on this pad).
        self.declare_parameter('axis_forward', 1)
        self.declare_parameter('axis_strafe', 3)
        self.declare_parameter('axis_yaw', 0)
        self.declare_parameter('invert_forward', True)
        self.declare_parameter('invert_strafe', False)
        self.declare_parameter('invert_yaw', False)
        # After stick release, ignore the planner this long (ms) so a twitchy
        # operator grab always interrupts a plan cleanly.
        self.declare_parameter('joy_release_guard_ms', 300)

        joy_topic = self.get_parameter('joy_topic').value
        planner_topic = self.get_parameter('planner_topic').value
        cmd_topic = self.get_parameter('cmd_vel_topic').value
        self._pub = self.create_publisher(Twist, cmd_topic, 10)
        self.create_subscription(Joy, joy_topic, self._on_joy, 10)
        self.create_subscription(Twist, planner_topic, self._on_planner, 10)
        self._joy_active = False
        self._joy_release_time = None
        self.get_logger().info(
            f'motion_control: joy {joy_topic} (priority) + planner {planner_topic} -> {cmd_topic}')

    def _axis(self, msg, idx, invert):
        if idx < 0 or idx >= len(msg.axes):
            return 0.0
        v = float(msg.axes[idx])
        if abs(v) < self.get_parameter('deadzone').value:
            return 0.0
        return -v if invert else v

    def _on_joy(self, msg: Joy):
        t = Twist()
        lin = self.get_parameter('max_linear').value
        ang = self.get_parameter('max_angular').value
        t.linear.x = self._axis(msg, self.get_parameter('axis_forward').value,
                                self.get_parameter('invert_forward').value) * lin
        t.linear.y = self._axis(msg, self.get_parameter('axis_strafe').value,
                                self.get_parameter('invert_strafe').value) * lin
        t.angular.z = self._axis(msg, self.get_parameter('axis_yaw').value,
                                 self.get_parameter('invert_yaw').value) * ang
        active = t.linear.x != 0.0 or t.linear.y != 0.0 or t.angular.z != 0.0
        if active:
            self._pub.publish(t)
            if not self._joy_active:
                self.get_logger().info('joystick took control')
            self._joy_active = True
        elif self._joy_active:
            self._pub.publish(t)          # one zero: stop now
            self._joy_active = False
            self._joy_release_time = self.get_clock().now()
            self.get_logger().info('joystick released — planner may drive')

    def _on_planner(self, msg: Twist):
        if self._joy_active:
            return                        # operator overrides the plan
        if self._joy_release_time is not None:
            guard_ns = int(self.get_parameter('joy_release_guard_ms').value) * 1_000_000
            if (self.get_clock().now() - self._joy_release_time).nanoseconds < guard_ns:
                return
        self._pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = MotionControl()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
