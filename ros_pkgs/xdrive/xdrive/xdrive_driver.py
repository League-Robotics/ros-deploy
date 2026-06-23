"""xdrive_driver: cmd_vel (Twist) -> X-drive inverse kinematics -> REV Hub motors.

Subscribes to a Twist, mixes it to four wheel powers (xdrive.kinematics) in the
order [front_left, front_right, rear_left, rear_right], maps each wheel to its
physical REV Hub motor port via `motor_ports`, and sends powers over serial via
the `rhsp` library. Publishes wheel encoder/velocity/effort as JointState.

Latency design (REV serial is ~slow; each transaction is a round-trip):
  * Commands are EVENT-DRIVEN — applied the instant a cmd_vel arrives, all four
    set_power calls back-to-back, so wheels start/stop together with minimal lag.
    set_power is only re-sent when the target changes (the hub holds the last
    value), so a steady stick costs no serial traffic.
  * A fast safety timer (in the SAME callback group as the command callback, so
    they never race on the wire) zeroes the motors if no cmd_vel arrives within
    cmd_timeout_ms (well under rhsp's 2.5 s hardware keep-alive fail-safe).
  * The slow bulk encoder read + JointState publish run on a SEPARATE low-rate
    timer in its OWN callback group, under a MultiThreadedExecutor, so feedback
    I/O can never add latency to the command path.
rhsp's connect() returns a Hub context manager whose keep-alive thread runs while
open and which fail_safes on exit; we also fail_safe on shutdown.
"""
import rclpy
from rclpy.node import Node
from rclpy.duration import Duration
from rclpy.executors import MultiThreadedExecutor
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from geometry_msgs.msg import Twist
from sensor_msgs.msg import JointState

import rhsp

from xdrive.kinematics import WheelConfig, mix

WHEEL_NAMES = ['front_left', 'front_right', 'rear_left', 'rear_right']
SAFETY_HZ = 25.0   # how often we check for a stale cmd_vel (cheap, no serial unless stale)


class XDriveDriver(Node):
    def __init__(self):
        super().__init__('xdrive_driver')
        self.declare_parameter('serial_port', '')          # ''/'auto' => enumerate
        self.declare_parameter('cmd_vel_topic', '/cmd_vel')
        self.declare_parameter('joint_state_topic', 'wheel_states')
        self.declare_parameter('max_power', 8000)
        self.declare_parameter('cmd_timeout_ms', 500)
        self.declare_parameter('feedback_rate_hz', 10.0)   # encoder publish rate
        self.declare_parameter('wheel_signs', [1, 1, 1, 1])
        self.declare_parameter('forward_scale', 1.0)
        self.declare_parameter('strafe_scale', 1.0)
        self.declare_parameter('yaw_scale', 1.0)
        # Physical hub motor port for each wheel, in WHEEL_NAMES order
        # [front_left, front_right, rear_left, rear_right].
        self.declare_parameter('motor_ports', [0, 1, 2, 3])

        self._cfg = WheelConfig(
            signs=tuple(int(s) for s in self.get_parameter('wheel_signs').value),
            max_power=int(self.get_parameter('max_power').value),
            forward_scale=float(self.get_parameter('forward_scale').value),
            strafe_scale=float(self.get_parameter('strafe_scale').value),
            yaw_scale=float(self.get_parameter('yaw_scale').value),
        )
        self._ports = [int(p) for p in self.get_parameter('motor_ports').value]
        self._timeout = Duration(
            seconds=self.get_parameter('cmd_timeout_ms').value / 1000.0)
        self._last_cmd = self.get_clock().now()
        self._last_applied = None

        # --- open the hub ---
        port = self.get_parameter('serial_port').value
        if not port or port == 'auto':
            hubs = rhsp.enumerate_hubs()
            if not hubs:
                raise RuntimeError('no REV hub found (rhsp.enumerate_hubs() empty)')
            port = hubs[0]
        self.get_logger().info(f'opening REV hub on {port}; wheel->port map {self._ports}')
        self.hub = rhsp.connect(port)
        self.hub.__enter__()                 # start keep-alive thread
        self.hub.init_peripherals()
        for ch in range(4):
            self.hub.motors[ch].set_mode(rhsp.MotorMode.CONSTANT_POWER)
            self.hub.motors[ch].set_power(0)
            self.hub.motors[ch].enable()
            self.hub.motors[ch].reset_encoder()
        self._last_applied = [0, 0, 0, 0]

        # Command + safety share one group (serialized, so _apply never races
        # itself); feedback is in its own group so its slow bulk read runs
        # concurrently and never delays a command.
        self._cmd_group = MutuallyExclusiveCallbackGroup()
        self._fb_group = MutuallyExclusiveCallbackGroup()
        self.create_subscription(
            Twist, self.get_parameter('cmd_vel_topic').value, self._on_cmd, 10,
            callback_group=self._cmd_group)
        self.create_timer(1.0 / SAFETY_HZ, self._safety_tick,
                          callback_group=self._cmd_group)
        self._js_pub = self.create_publisher(
            JointState, self.get_parameter('joint_state_topic').value, 10)
        self.create_timer(
            1.0 / float(self.get_parameter('feedback_rate_hz').value),
            self._feedback_tick, callback_group=self._fb_group)
        self.get_logger().info('xdrive_driver ready')

    def _apply(self, powers):
        """Send the four wheel powers to their ports (only if changed)."""
        if powers == self._last_applied:
            return
        try:
            for wheel_i, p in enumerate(powers):
                self.hub.motors[self._ports[wheel_i]].set_power(int(p))
        except Exception as exc:
            self.get_logger().warning(f'set_power failed: {exc}')
            return
        self._last_applied = list(powers)
        self.get_logger().debug(f'applied wheel powers {powers}')

    def _on_cmd(self, msg: Twist):
        # REP-103: linear.x forward, linear.y left (strafe), angular.z yaw CCW.
        self._last_cmd = self.get_clock().now()
        self._apply(mix(forward=msg.linear.x, strafe=msg.linear.y,
                        yaw=msg.angular.z, cfg=self._cfg))

    def _safety_tick(self):
        if (self.get_clock().now() - self._last_cmd) > self._timeout:
            self._apply([0, 0, 0, 0])

    def _feedback_tick(self):
        try:
            bulk = self.hub.bulk_input()
            positions = [float(getattr(bulk, f'motor{self._ports[w]}_encoder')) for w in range(4)]
            velocities = [float(getattr(bulk, f'motor{self._ports[w]}_velocity')) for w in range(4)]
        except Exception as exc:
            self.get_logger().warning(f'bulk_input failed: {exc}')
            return
        js = JointState()
        js.header.stamp = self.get_clock().now().to_msg()
        js.name = WHEEL_NAMES
        js.position = positions
        js.velocity = velocities
        js.effort = [float(p) for p in (self._last_applied or [0, 0, 0, 0])]
        self._js_pub.publish(js)

    def destroy_node(self):
        try:
            if hasattr(self, 'hub'):
                self.hub.fail_safe()
                self.hub.__exit__(None, None, None)
        except Exception:
            pass
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = None
    executor = MultiThreadedExecutor(num_threads=3)
    try:
        node = XDriveDriver()
        executor.add_node(node)
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.try_shutdown()
