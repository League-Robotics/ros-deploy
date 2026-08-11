"""xdrive_driver: cmd_vel (Twist) -> X-drive inverse kinematics -> REV Hub motors.

Subscribes to a Twist, mixes it to four wheel powers (xdrive.kinematics) in the
order [front_left, front_right, rear_left, rear_right], maps each wheel to its
physical REV Hub motor port via `motor_ports`, and sends powers over serial via
the `rhsp` library. Publishes wheel encoder/velocity/effort as JointState.

Latency design (REV serial is ~slow; each transaction is a round-trip):
  * Commands are COALESCED, not per-message: _on_cmd only records the newest
    target; the 25 Hz safety timer (same callback group, so they never race on
    the wire) is the sole serial writer, applying the latest target and zeroing
    the motors if no cmd_vel arrives within cmd_timeout_ms (well under rhsp's
    2.5 s hardware keep-alive fail-safe). A real joystick emits 100+ Hz of
    jittering cmd_vel; applying each one saturated the serial link and starved
    the keep-alive into hub fail-safe mid-drive (2026-08-03). Powers are also
    quantized so stick noise dedupes into zero serial traffic.
  * The slow bulk encoder read + JointState publish run on a SEPARATE low-rate
    timer in its OWN callback group, under a MultiThreadedExecutor, so feedback
    I/O can never add latency to the command path.
rhsp's connect() returns a Hub context manager whose keep-alive thread runs while
open and which fail_safes on exit; we also fail_safe on shutdown.
"""
import time

import rclpy
from rclpy.node import Node
from rclpy.duration import Duration
from rclpy.executors import MultiThreadedExecutor
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from geometry_msgs.msg import Twist
from sensor_msgs.msg import JointState

import rhsp
import serial

from revhub.kinematics import WheelConfig, mix

WHEEL_NAMES = ['front_left', 'front_right', 'rear_left', 'rear_right']
SAFETY_HZ = 25.0   # sole motor-apply rate: latest target only, ≤25 serial bursts/s
POWER_QUANTUM = 300  # ~1% of full scale: stick jitter quantizes away to no traffic

# Hub status bits that mean "outputs are disabled": the hub stopped receiving
# keep-alives and dropped into fail-safe (its LED blinks blue). rhsp's heartbeat
# thread swallows send failures silently, so these bits — not an exception —
# are the only in-band evidence of a dead session.
ROUTINE_FAULT_BITS = int(rhsp.ModuleStatusBits.KeepAliveTimeout) | int(rhsp.ModuleStatusBits.FailSafe)


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
        self._target = [0, 0, 0, 0]

        # --- open the hub (validated: see _open_validated_session) ---
        port = self.get_parameter('serial_port').value
        if not port or port == 'auto':
            hubs = rhsp.enumerate_hubs()
            if not hubs:
                raise RuntimeError('no REV hub found (rhsp.enumerate_hubs() empty)')
            port = hubs[0]
        self._hub_port = port
        self._fb_failures = 0
        self._status_strikes = 0
        self._fb_count = 0
        self.get_logger().info(f'opening REV hub on {port}; wheel->port map {self._ports}')
        self.hub = self._open_validated_session()
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

    def _open_validated_session(self):
        """Connect to the hub and PROVE the session is healthy before trusting it.

        A session inherits corruption from an uncleanly-ended predecessor
        (stale half-frames in the FTDI buffers after a crash, SIGKILL, or cold
        power-on). The symptoms never raise: writes are silently lost (hub in
        fail-safe, LED blinking blue) or every transaction crawls through
        checksum retries (multi-second bulk reads). Connecting blind therefore
        yields a driver that logs "ready" but cannot drive — observed
        repeatedly on baldur 2026-08-03. So: flush the tty first, connect, then
        require a fast bulk_input round-trip AND a clean status register after
        the keep-alive has had longer than the hub's 2.5 s fail-safe window to
        prove itself. Anything less gets torn down and retried.
        """
        last_exc = None
        for attempt in range(1, 6):
            hub = None
            try:
                # Discard garbage a dead predecessor left in the FTDI buffers —
                # this, not the hub itself, is what poisons fresh sessions.
                s = serial.Serial(self._hub_port, 460800, timeout=0.2)
                s.reset_input_buffer()
                s.reset_output_buffer()
                s.close()
                # timeout=0.15 s, not the 1.0 s default: motor EMI corrupts
                # frames while driving, and every corrupted transaction burns a
                # full timeout before retrying. Healthy round-trips are ~3 ms,
                # so 150 ms keeps 50x margin while a corruption convoy resolves
                # 6x faster — fast enough that keep-alives can't be starved
                # past the hub's 2.5 s fail-safe deadline.
                hub = rhsp.connect(self._hub_port, timeout=0.15)
                # 0.5 s heartbeat (library default 2.0): against the 2.5 s
                # deadline it now takes FIVE consecutive corrupted keep-alives
                # to trip fail-safe, instead of one.
                hub.start_keepalive(interval=0.5)
                hub.init_peripherals()
                for ch in range(4):
                    hub.motors[ch].set_mode(rhsp.MotorMode.CONSTANT_POWER)
                    hub.motors[ch].set_power(0)
                    hub.motors[ch].enable()
                    hub.motors[ch].reset_encoder()
                hub.get_module_status(clear=True)   # drop latches from before us
                t0 = time.monotonic()
                hub.bulk_input()
                bulk_ms = (time.monotonic() - t0) * 1000.0
                time.sleep(3.0)   # > 2.5 s fail-safe: keep-alive must hold the hub
                status = int(hub.get_module_status(clear=True).status_bits)
                battery_mv = hub.battery_voltage_mv()
                if bulk_ms > 150.0:
                    raise RuntimeError(f'bulk_input took {bulk_ms:.0f} ms (desynced serial)')
                if status & ROUTINE_FAULT_BITS:
                    raise RuntimeError(f'keep-alive not holding (status=0x{status:x})')
                self.get_logger().info(
                    f'hub session validated (attempt {attempt}): bulk={bulk_ms:.0f}ms '
                    f'battery={battery_mv}mV status=0x{status:x}')
                if battery_mv < 9000:
                    self.get_logger().warning(
                        f'hub battery low ({battery_mv} mV) — motors may brown out')
                return hub
            except Exception as exc:
                last_exc = exc
                self.get_logger().warning(f'hub session attempt {attempt} failed: {exc}')
                if hub is not None:
                    try:
                        hub.__exit__(None, None, None)
                    except Exception:
                        pass
                time.sleep(2.0)
        raise RuntimeError(f'no healthy hub session after 5 attempts: {last_exc}')

    def _recover_session(self, why: str):
        """Tear down a rotten session and re-establish a validated one."""
        self.get_logger().error(f'hub session unhealthy: {why}; reconnecting')
        try:
            self.hub.__exit__(None, None, None)
        except Exception:
            pass
        self._fb_failures = 0
        self._status_strikes = 0
        # Raises after 5 failed attempts — the process dies and systemd restarts
        # us into another validated attempt, so a flapping hub cannot leave a
        # zombie driver behind.
        self.hub = self._open_validated_session()
        self._last_applied = None   # force the next _apply to resend powers

    def _apply(self, powers):
        """Send the four wheel powers to their ports (quantized; only if changed)."""
        powers = [int(round(p / POWER_QUANTUM) * POWER_QUANTUM) for p in powers]
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
        # Record only — the safety tick is the sole serial writer (see header).
        self._last_cmd = self.get_clock().now()
        self._target = mix(forward=msg.linear.x, strafe=msg.linear.y,
                           yaw=msg.angular.z, cfg=self._cfg)

    def _safety_tick(self):
        if (self.get_clock().now() - self._last_cmd) > self._timeout:
            self._target = [0, 0, 0, 0]
        self._apply(self._target)

    def _feedback_tick(self):
        try:
            bulk = self.hub.bulk_input()
            positions = [float(getattr(bulk, f'motor{self._ports[w]}_encoder')) for w in range(4)]
            velocities = [float(getattr(bulk, f'motor{self._ports[w]}_velocity')) for w in range(4)]
        except Exception as exc:
            self.get_logger().warning(f'bulk_input failed: {exc}')
            self._fb_failures += 1
            if self._fb_failures >= 20:      # ~2 s of nothing but failures
                self._recover_session('bulk_input failing continuously')
            return
        self._fb_failures = 0
        js = JointState()
        js.header.stamp = self.get_clock().now().to_msg()
        js.name = WHEEL_NAMES
        js.position = positions
        js.velocity = velocities
        js.effort = [float(p) for p in (self._last_applied or [0, 0, 0, 0])]
        self._js_pub.publish(js)
        # Every ~5 s, check the hub is actually being kept alive. A desynced
        # session can keep answering reads while every keep-alive is lost —
        # the hub sits in fail-safe (LED blinking blue) ignoring motor power,
        # and nothing raises. These latched bits are the only in-band tell.
        self._fb_count += 1
        if self._fb_count % 20 == 0:   # every ~2 s: a blip re-arms fast
            try:
                status = int(self.hub.get_module_status(clear=True).status_bits)
            except Exception:
                status = None
            if status is None or status & ROUTINE_FAULT_BITS:
                self._status_strikes += 1
                if self._status_strikes >= 2:
                    self._recover_session(
                        f'hub in fail-safe (status={status if status is None else hex(status)})')
                elif status is not None:
                    # A single latched fail-safe = a transient blip the keep-alive
                    # already recovered from — BUT the hub left the motor channels
                    # DISABLED. Seen 2026-08-03: LED green, reads fine, every
                    # set_power silently ignored until re-enabled. Re-arm now, and
                    # say so — a silent strike here ate the only evidence last time.
                    self.get_logger().warning(
                        f'hub fail-safe blip (status={hex(status)}); re-enabling motor channels')
                    try:
                        for ch in range(4):
                            self.hub.motors[ch].set_mode(rhsp.MotorMode.CONSTANT_POWER)
                            self.hub.motors[ch].enable()
                        self._last_applied = None   # resend powers on next command
                    except Exception as exc:
                        self.get_logger().warning(f'motor re-enable failed: {exc}')
            else:
                self._status_strikes = 0

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
