"""twist_to_swerve: geometry_msgs/Twist -> per-module swerve joint commands.

The Gazebo side of a swerve robot is 8 independent joint controllers (a
position-controlled steer and a velocity-controlled drive per corner) — no
Gazebo system does the mixing, so this node is the drivetrain: it subscribes
one body-frame Twist and publishes a Float64 per joint topic. ros_gz_bridge
carries those topics into the simulator (see roles/gz_bridge).

Modules are pure configuration — positions, zero-steer headings phi0, topics —
so the same node drives any swerve model whose corners are described in its
parameters. The values for the patribots model are derived in its UPSTREAM.md
and wired up in inventory/host_vars/buzzkill.yml.

Safety shape mirrors the fleet's other command nodes: commands are forwarded
as they arrive, and a watchdog zeroes every drive wheel if the Twist source
goes quiet for command_timeout_s. Steer angles are held, not zeroed — recentre
on stop is how a swerve robot lurches.
"""
import math

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from std_msgs.msg import Float64

from .kinematics import Module, mix


class TwistToSwerve(Node):
    def __init__(self):
        super().__init__('twist_to_swerve')
        self.declare_parameter('cmd_vel_topic', '/sim/swerve/cmd_vel')
        self.declare_parameter('wheel_radius', 0.0508)
        self.declare_parameter('command_timeout_s', 0.5)
        # Module list; each name needs <name>.x, .y, .phi0_deg, .steer_topic,
        # .drive_topic (same parameter layout as joint_teleop's joints).
        self.declare_parameter('modules', ['front_left', 'front_right',
                                           'rear_left', 'rear_right'])

        names = self.get_parameter('modules').value
        self._modules = []
        self._steer_pubs = []
        self._drive_pubs = []
        for n in names:
            self.declare_parameter(f'{n}.x', 0.0)
            self.declare_parameter(f'{n}.y', 0.0)
            self.declare_parameter(f'{n}.phi0_deg', 0.0)
            self.declare_parameter(f'{n}.steer_topic', f'/model/robot/{n}/steer/cmd_pos')
            self.declare_parameter(f'{n}.drive_topic', f'/model/robot/{n}/drive/cmd_vel')
            self._modules.append(Module(
                name=n,
                x=self.get_parameter(f'{n}.x').value,
                y=self.get_parameter(f'{n}.y').value,
                phi0=math.radians(self.get_parameter(f'{n}.phi0_deg').value)))
            self._steer_pubs.append(self.create_publisher(
                Float64, self.get_parameter(f'{n}.steer_topic').value, 10))
            self._drive_pubs.append(self.create_publisher(
                Float64, self.get_parameter(f'{n}.drive_topic').value, 10))

        self._prev_steer = [0.0] * len(self._modules)
        self._moving = False
        self._last_cmd_time = self.get_clock().now()

        topic = self.get_parameter('cmd_vel_topic').value
        self.create_subscription(Twist, topic, self._on_twist, 10)
        self.create_timer(0.1, self._watchdog)
        self.get_logger().info(
            f'twist_to_swerve: {topic} -> ' +
            ', '.join(m.name for m in self._modules))

    def _publish(self, commands):
        for i, (steer, drive) in enumerate(commands):
            self._steer_pubs[i].publish(Float64(data=steer))
            self._drive_pubs[i].publish(Float64(data=drive))
            self._prev_steer[i] = steer

    def _on_twist(self, t: Twist):
        self._last_cmd_time = self.get_clock().now()
        commands = mix(t.linear.x, t.linear.y, t.angular.z,
                       self._modules,
                       self.get_parameter('wheel_radius').value,
                       self._prev_steer)
        self._moving = any(d != 0.0 for _, d in commands)
        self._publish(commands)

    def _watchdog(self):
        """Stop the wheels if the Twist source dies mid-command."""
        if not self._moving:
            return
        age = (self.get_clock().now() - self._last_cmd_time).nanoseconds * 1e-9
        if age > self.get_parameter('command_timeout_s').value:
            self._publish([(self._prev_steer[i], 0.0)
                           for i in range(len(self._modules))])
            self._moving = False
            self.get_logger().warning(
                f'no Twist for {age:.1f}s — drives zeroed, steer held')


def main(args=None):
    rclpy.init(args=args)
    node = TwistToSwerve()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
