"""local_teleop: joystick on THIS host -> REV Hub directly. No ROS in the loop.

Born 2026-08-04: the networked chain (ros-joy -> joy_to_twist -> WiFi ->
xdrive_driver) worked but carried the fleet WiFi's multi-hundred-ms latency
spikes straight into the driving feel. This collapses the control path to one
process on the robot: read /dev/input/js* raw (joydev protocol, no SDL, no
ROS), mix with xdrive.kinematics, apply slew-limited quantized powers to the
hub. ROS is used ONLY to publish wheel encoders as JointState for observers —
if rclpy is broken or absent, driving is unaffected.

Keeps every hub-session lesson from the ROS driver (see xdrive_driver.py):
validated session establishment (tty flush, 150 ms transaction timeout, 0.5 s
keep-alive, fail-safe-hold check), runtime fail-safe monitor with motor
re-enable on a single blip and full reconnect on two, coalesced apply with
power quantization, and slew limiting so stick steps can't brown the 12 V rail.

The joystick may vanish at any time (2.4 GHz pads sleep and their dongle
re-enumerates): motors are zeroed while it is gone and the device is re-opened
when it returns.
"""
import argparse
import glob
import logging
import signal
import struct
import sys
import threading
import time

import rhsp
import serial

from xdrive.kinematics import WheelConfig, mix

log = logging.getLogger('local_teleop')

JS_EVENT_SIZE = 8          # joydev: u32 time_ms, s16 value, u8 type, u8 number
JS_EVENT_AXIS = 0x02       # (JS_EVENT_INIT 0x80 may be OR'd in — keep it)

ROUTINE_FAULT_BITS = int(rhsp.ModuleStatusBits.KeepAliveTimeout) | int(rhsp.ModuleStatusBits.FailSafe)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    tobool = lambda s: str(s).lower() in ('1', 'true', 'yes')
    p.add_argument('--device', default='/dev/input/js0',
                   help='joystick device path or glob (first match wins)')
    p.add_argument('--axis-forward', type=int, default=1)
    p.add_argument('--axis-strafe', type=int, default=0)
    p.add_argument('--axis-yaw', type=int, default=3)
    p.add_argument('--invert-forward', type=tobool, default=True)
    p.add_argument('--invert-strafe', type=tobool, default=True)
    p.add_argument('--invert-yaw', type=tobool, default=True)
    p.add_argument('--deadzone', type=float, default=0.08)
    p.add_argument('--control-mode', choices=('velocity', 'power'), default='velocity',
                   help='velocity: hub-firmware PID per wheel (synchronized, torque '
                        'under load); power: open-loop duty (no load compensation)')
    p.add_argument('--max-velocity', type=int, default=2500,
                   help='velocity mode: full-stick target in encoder counts/s')
    p.add_argument('--max-power', type=int, default=8000,
                   help='power mode only: full-stick duty in rhsp units')
    p.add_argument('--motor-ports', default='0,1,2,3',
                   help='hub port per wheel [fl,fr,rl,rr]')
    p.add_argument('--wheel-signs', default='1,1,1,1')
    p.add_argument('--wheel-scales', default='1,1,1,1',
                   help='per-wheel velocity multiplier [fl,fr,rl,rr] — corrects a '
                        'wheel whose gearbox has different counts/rev (equal '
                        'ticks/s there means unequal wheel speed)')
    p.add_argument('--forward-scale', type=float, default=1.0)
    p.add_argument('--strafe-scale', type=float, default=1.0)
    p.add_argument('--yaw-scale', type=float, default=1.0)
    p.add_argument('--serial-port', default='auto')
    p.add_argument('--apply-hz', type=float, default=50.0)
    p.add_argument('--slew', type=int, default=1500,
                   help='max power change per wheel per apply tick')
    p.add_argument('--quantum', type=int, default=300,
                   help='power quantization step (stick noise dedupes away)')
    p.add_argument('--feedback-hz', type=float, default=10.0)
    p.add_argument('--joint-topic', default='wheel_states')
    p.add_argument('--cal-buttons', default='3:0,2:1,1:2,0:3',
                   help='motor-identification map "buttonIdx:hubPort,..." — holding '
                        'the button drives that hub port forward at --cal-velocity. '
                        'Default fits 8BitDo/XInput: Y(3)->0, X(2)->1, B(1)->2, A(0)->3')
    p.add_argument('--cal-velocity', type=int, default=800,
                   help='counts/s (or duty units in power mode) for button-driven motor ID')
    return p.parse_args(argv)


class LocalTeleop:
    def __init__(self, args):
        self.args = args
        self.velocity_mode = args.control_mode == 'velocity'
        self.cfg = WheelConfig(
            signs=tuple(int(s) for s in args.wheel_signs.split(',')),
            # mix() scales into whatever unit the mode commands: encoder
            # counts/s (velocity) or duty units (power).
            max_power=args.max_velocity if self.velocity_mode else args.max_power,
            forward_scale=args.forward_scale,
            strafe_scale=args.strafe_scale,
            yaw_scale=args.yaw_scale,
        )
        self.ports = [int(s) for s in args.motor_ports.split(',')]
        self.scales = [float(s) for s in args.wheel_scales.split(',')]
        self.stop = threading.Event()
        self.axes = {}
        self.buttons = {}
        self.cal_map = {}
        for pair in (args.cal_buttons or '').split(','):
            if ':' in pair:
                b, port = pair.split(':')
                self.cal_map[int(b)] = int(port)
        self._last_cal = None
        self.js_ok = False
        self.applied = [0, 0, 0, 0]
        self.hub = None
        self.vels = None       # velocity mode: HubVelocityController per WHEEL
        self._strikes = 0

    # ── hub session (validated — same contract as xdrive_driver) ──────────
    def open_validated_session(self):
        port = self.args.serial_port
        if not port or port == 'auto':
            hubs = rhsp.enumerate_hubs()
            if not hubs:
                raise RuntimeError('no REV hub found')
            port = hubs[0]
        last_exc = None
        for attempt in range(1, 6):
            hub = None
            try:
                s = serial.Serial(port, 460800, timeout=0.2)
                s.reset_input_buffer(); s.reset_output_buffer(); s.close()
                hub = rhsp.connect(port, timeout=0.15)
                hub.start_keepalive(interval=0.5)
                hub.init_peripherals()
                for ch in range(4):
                    hub.motors[ch].reset_encoder()
                if self.velocity_mode:
                    # Hub-firmware PID per wheel: attach() handles the firmware
                    # ordering quirk (zero target BEFORE enable, else NACK 50)
                    # and float-at-zero so released sticks coast, not brake.
                    vels = [rhsp.HubVelocityController(hub, self.ports[w]) for w in range(4)]
                    for c in vels:
                        c.attach()
                    self.vels = vels
                else:
                    for ch in range(4):
                        hub.motors[ch].set_mode(rhsp.MotorMode.CONSTANT_POWER)
                        hub.motors[ch].set_power(0)
                        hub.motors[ch].enable()
                hub.get_module_status(clear=True)
                t0 = time.monotonic(); hub.bulk_input()
                bulk_ms = (time.monotonic() - t0) * 1000.0
                time.sleep(3.0)      # keep-alive must hold past the 2.5 s deadline
                status = int(hub.get_module_status(clear=True).status_bits)
                battery_mv = hub.battery_voltage_mv()
                if bulk_ms > 150.0:
                    raise RuntimeError(f'bulk_input took {bulk_ms:.0f} ms (desynced serial)')
                if status & ROUTINE_FAULT_BITS:
                    raise RuntimeError(f'keep-alive not holding (status=0x{status:x})')
                log.info('hub session validated (attempt %d): bulk=%.0fms battery=%dmV status=0x%x',
                         attempt, bulk_ms, battery_mv, status)
                if battery_mv < 9000:
                    log.warning('hub battery low (%d mV) — motors may brown out', battery_mv)
                self.applied = [0, 0, 0, 0]
                return hub
            except Exception as exc:
                last_exc = exc
                log.warning('hub session attempt %d failed: %s', attempt, exc)
                if hub is not None:
                    try:
                        hub.__exit__(None, None, None)
                    except Exception:
                        pass
                time.sleep(2.0)
        raise RuntimeError(f'no healthy hub session after 5 attempts: {last_exc}')

    def recover_session(self, why):
        log.error('hub session unhealthy: %s; reconnecting', why)
        try:
            self.hub.__exit__(None, None, None)
        except Exception:
            pass
        self._strikes = 0
        self.hub = self.open_validated_session()   # raises -> main exits -> systemd restarts

    # ── joystick reader thread ─────────────────────────────────────────────
    def js_reader(self):
        while not self.stop.is_set():
            # Exclude evdev nodes: by-id publishes BOTH "...-joystick" (joydev,
            # what we parse) and "...-event-joystick" (evdev, different struct,
            # root:input only) — and the event one sorts FIRST. Bitten 2026-08-04.
            matches = sorted(m for m in glob.glob(self.args.device) if '-event-' not in m)
            if not matches:
                if self.js_ok:
                    log.warning('joystick gone (%s) — motors zeroed until it returns',
                                self.args.device)
                self.js_ok = False
                self.axes = {}
                self.stop.wait(2.0)
                continue
            path = matches[0]
            try:
                with open(path, 'rb', buffering=0) as f:
                    log.info('joystick opened: %s', path)
                    self.js_ok = True
                    while not self.stop.is_set():
                        b = f.read(JS_EVENT_SIZE)
                        if not b or len(b) < JS_EVENT_SIZE:
                            break
                        _, value, etype, num = struct.unpack('IhBB', b)
                        if etype & JS_EVENT_AXIS:
                            self.axes[num] = value / 32767.0
                        elif etype & 0x01:            # JS_EVENT_BUTTON
                            self.buttons[num] = bool(value)
            except OSError as exc:
                log.warning('joystick read failed (%s): %s', path, exc)
            self.js_ok = False
            self.axes = {}
            self.stop.wait(1.0)

    # ── control loop (main thread; the ONLY motor writer) ─────────────────
    def axis(self, idx, invert):
        v = self.axes.get(idx, 0.0)
        if abs(v) < self.args.deadzone:
            return 0.0
        return -v if invert else v

    def run_control(self):
        a = self.args
        period = 1.0 / a.apply_hz
        status_every = max(1, int(2.0 * a.apply_hz))     # status check ~every 2 s
        tick = 0
        while not self.stop.is_set():
            t0 = time.monotonic()
            cal = None
            if self.js_ok:
                for b, port in self.cal_map.items():
                    if self.buttons.get(b):
                        cal = (b, port)
                        break
            if cal != self._last_cal:
                if cal:
                    log.info('CAL: button %d held -> hub port %d FORWARD at %d',
                             cal[0], cal[1], a.cal_velocity)
                else:
                    log.info('CAL: released — sticks back in control')
                self._last_cal = cal
            if cal:
                target = [a.cal_velocity if self.ports[w] == cal[1] else 0 for w in range(4)]
            elif self.js_ok:
                target = mix(forward=self.axis(a.axis_forward, a.invert_forward),
                             strafe=self.axis(a.axis_strafe, a.invert_strafe),
                             yaw=self.axis(a.axis_yaw, a.invert_yaw), cfg=self.cfg)
                target = [int(t * s) for t, s in zip(target, self.scales)]
            else:
                target = [0, 0, 0, 0]
            self.slew_apply(target)
            tick += 1
            if tick % status_every == 0:
                self.check_hub_status()
            dt = time.monotonic() - t0
            self.stop.wait(max(0.0, period - dt))
        # shutdown: outputs off
        try:
            self.hub.fail_safe()
            self.hub.__exit__(None, None, None)
        except Exception:
            pass

    def slew_apply(self, target):
        q = self.args.quantum
        out = list(self.applied)
        for i in range(4):
            step = max(-self.args.slew, min(self.args.slew, target[i] - self.applied[i]))
            out[i] = int(round((self.applied[i] + step) / q) * q)
        try:
            for i in range(4):
                if out[i] != self.applied[i]:
                    if self.velocity_mode:
                        self.vels[i].command(out[i])
                    else:
                        self.hub.motors[self.ports[i]].set_power(out[i])
                    self.applied[i] = out[i]
        except Exception as exc:
            log.warning('motor command failed: %s', exc)

    def check_hub_status(self):
        try:
            status = int(self.hub.get_module_status(clear=True).status_bits)
        except Exception:
            status = None
        if status is None or status & ROUTINE_FAULT_BITS:
            self._strikes += 1
            if self._strikes >= 2:
                self.recover_session(
                    f'hub in fail-safe (status={status if status is None else hex(status)})')
            elif status is not None:
                log.warning('hub fail-safe blip (status=%s); re-enabling motor channels',
                            hex(status))
                try:
                    if self.velocity_mode:
                        for c in self.vels:
                            c.attach()   # re-enters CONSTANT_VELOCITY + zero + enable
                    else:
                        for ch in range(4):
                            self.hub.motors[ch].set_mode(rhsp.MotorMode.CONSTANT_POWER)
                            self.hub.motors[ch].enable()
                    self.applied = [0, 0, 0, 0]   # resend targets from zero (slewed)
                except Exception as exc:
                    log.warning('motor re-enable failed: %s', exc)
        else:
            self._strikes = 0

    # ── optional ROS feedback thread (never in the control path) ──────────
    def ros_feedback(self):
        try:
            import rclpy
            from sensor_msgs.msg import JointState
            rclpy.init()
            node = rclpy.create_node('xdrive_local_teleop')
            pub = node.create_publisher(JointState, self.args.joint_topic, 10)
            log.info('ROS feedback: publishing %s at %.1f Hz',
                     self.args.joint_topic, self.args.feedback_hz)
        except Exception as exc:
            log.warning('ROS feedback disabled (%s) — driving unaffected', exc)
            return
        names = ['front_left', 'front_right', 'rear_left', 'rear_right']
        while not self.stop.is_set():
            self.stop.wait(1.0 / self.args.feedback_hz)
            try:
                bulk = self.hub.bulk_input()
                js = JointState()
                js.header.stamp = node.get_clock().now().to_msg()
                js.name = names
                js.position = [float(getattr(bulk, f'motor{self.ports[w]}_encoder')) for w in range(4)]
                js.velocity = [float(getattr(bulk, f'motor{self.ports[w]}_velocity')) for w in range(4)]
                js.effort = [float(p) for p in self.applied]
                pub.publish(js)
            except Exception:
                pass   # feedback is best-effort; the control loop owns recovery


def main(argv=None):
    logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                        format='[%(levelname)s] [%(name)s]: %(message)s')
    args = parse_args(argv)
    t = LocalTeleop(args)
    signal.signal(signal.SIGTERM, lambda *_: t.stop.set())
    signal.signal(signal.SIGINT, lambda *_: t.stop.set())
    t.hub = t.open_validated_session()
    threading.Thread(target=t.js_reader, daemon=True, name='js-reader').start()
    threading.Thread(target=t.ros_feedback, daemon=True, name='ros-feedback').start()
    log.info('local teleop ready: %s -> hub ports %s (%s mode, full-stick=%d, slew=%d/tick @%.0fHz)',
             args.device, t.ports, args.control_mode,
             args.max_velocity if t.velocity_mode else args.max_power,
             args.slew, args.apply_hz)
    t.run_control()
    return 0


if __name__ == '__main__':
    sys.exit(main())
