"""joy_to_joints: gamepad -> joint POSITION commands (std_msgs/Float64).

Separate from the drive teleop packages on purpose. Driving is a velocity
command to a chassis; this is position control of articulated joints — robot
flippers, an arm, a gripper — and the two have different failure modes and
different safe defaults. A drive command that is lost means the robot coasts to
a stop; a joint command that is lost leaves the joint where it was.

Control is RATE-BASED, not absolute: a stick or D-pad held in one direction
moves the target angle at `rate` rad/s until released, and the target then
stays put. Mapping an axis straight to an angle would instead snap joints to a
new pose the instant the node starts, and would make a resting-at-full-
deflection axis (an analog trigger) drive the joint to its limit unbidden.

Joints are declared by name and configured with nested parameters, so one node
drives any number of them:

    joints:                 ['front_left', 'front_right']
    front_left.topic:       /model/robot/flipper/front_left/cmd_pos
    front_left.axis:        7        # joystick axis that moves it
    front_left.invert:      false
    front_left.scale:       1.0      # flip to -1.0 to mirror a paired joint

Axis indices and signs are not portable between pads; read them off the
hardware with `ros2 run diffdrive_teleop joy_probe`.
"""
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Joy
from std_msgs.msg import Float64


class JoyToJoints(Node):
    def __init__(self):
        super().__init__('joy_to_joints')
        self.declare_parameter('joy_topic', '/joy')
        self.declare_parameter('joints', [''])
        self.declare_parameter('rate', 1.0)          # rad/s while held
        self.declare_parameter('deadzone', 0.5)      # D-pads are -1/0/+1
        self.declare_parameter('min_angle', -1.2)
        self.declare_parameter('max_angle', 1.2)
        self.declare_parameter('publish_hz', 20.0)
        self.declare_parameter('home_button', -1)    # -1 = no home button

        names = [n for n in self.get_parameter('joints').value if n]
        self._joints = []
        for n in names:
            self.declare_parameter(f'{n}.topic', f'/{n}/cmd_pos')
            self.declare_parameter(f'{n}.axis', -1)
            self.declare_parameter(f'{n}.invert', False)
            self.declare_parameter(f'{n}.scale', 1.0)
            self._joints.append({
                'name': n,
                'axis': self.get_parameter(f'{n}.axis').value,
                'invert': self.get_parameter(f'{n}.invert').value,
                'scale': self.get_parameter(f'{n}.scale').value,
                'target': 0.0,
                'pub': self.create_publisher(
                    Float64, self.get_parameter(f'{n}.topic').value, 10),
            })

        self._axes = []
        self._buttons = []
        self.create_subscription(Joy, self.get_parameter('joy_topic').value,
                                 self._on_joy, 10)
        # Integrate and publish on a fixed timer rather than per Joy message:
        # the pad's autorepeat rate is not a control rate, and a pad that stops
        # autorepeating would otherwise freeze the integration mid-motion.
        hz = self.get_parameter('publish_hz').value
        self._dt = 1.0 / hz
        self.create_timer(self._dt, self._tick)
        self.get_logger().info(
            f'joy_to_joints: {len(self._joints)} joint(s) '
            f'[{", ".join(j["name"] for j in self._joints)}] '
            f'at {self.get_parameter("rate").value} rad/s')

    def _on_joy(self, msg: Joy):
        self._axes = list(msg.axes)
        self._buttons = list(msg.buttons)

    def _tick(self):
        if not self._axes:
            return
        hb = self.get_parameter('home_button').value
        home = 0 <= hb < len(self._buttons) and self._buttons[hb] == 1

        dz = self.get_parameter('deadzone').value
        rate = self.get_parameter('rate').value
        lo = self.get_parameter('min_angle').value
        hi = self.get_parameter('max_angle').value

        for j in self._joints:
            if home:
                j['target'] = 0.0
            else:
                a = j['axis']
                v = self._axes[a] if 0 <= a < len(self._axes) else 0.0
                if abs(v) >= dz:
                    if j['invert']:
                        v = -v
                    j['target'] += v * j['scale'] * rate * self._dt
                    j['target'] = max(lo, min(hi, j['target']))
            m = Float64()
            m.data = float(j['target'])
            j['pub'].publish(m)


def main(args=None):
    rclpy.init(args=args)
    node = JoyToJoints()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
