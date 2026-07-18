---
title: X-drive Teleop — Driving the REV Hub Robots
blurb: Running, verifying, and troubleshooting the REV Hub drive robots (baldur, nepr) and the golem operator station.
order: 37
updated: 2026-07-18
tags: [ros, xdrive, revhub, teleop, serial]
---

# X-drive Teleop — Driving the REV Hub Robots

*Verified 2026-07-18 on live hardware. Source of truth for config: this repo
(`roles/revhub`, `roles/xdrive`, `ros_pkgs/xdrive`).*

## What this is

Two Raspberry Pi robots carry a REV Robotics Expansion Hub wired to four drive
motors (X-drive: omni wheels at 45°, so the robot translates in any direction
while rotating). The pipeline, end to end:

```
gamepad (golem)                     robot Pi (baldur or nepr)
  joy-to-twist service                xdrive-driver service
       │                                   │
       └── Twist on <cmd_vel topic> ──────►│ rhsp (REV Hub Serial Protocol)
                                           └──► /dev/revhub (USB FTDI) ──► motors
                                           ◄─── encoders → JointState on <wheel_states topic>
```

Everything runs on **ROS 2 domain 42** over the **Busboom Mesh** WiFi.
The hub speaks a 460800-baud serial protocol via the `rhsp` Python library
(from `League-Robotics/python-serial-hub-control`; installed by `roles/revhub`).

## The players

| Host | IP | Runs | Listens on | Publishes | Calibration |
|------|----|------|-----------|-----------|-------------|
| `golem` | 192.168.1.173 | `joy-to-twist` (operator) | `/golem/joy/joystick1` | `/cmd_vel` | Logitech Dual Action, **ANALOG mode required** |
| `baldur` | 192.168.1.152 | `xdrive-driver` (robot) | `/cmd_vel` | `/wheel_states` | Calibrated: ports `[2,3,1,0]`, max_power 16000 |
| `nepr` | 192.168.1.146 | `xdrive-driver` (robot) | `/nepr/cmd_vel` | `/nepr/wheel_states` | **Not calibrated** — default ports `[0,1,2,3]`, bench-safe max_power 4000 (may not overcome stiction) |

Topic ownership rule: **baldur owns the bare `/cmd_vel` and `/wheel_states`**;
every other robot gets `/<host>/cmd_vel` and `/<host>/wheel_states` (set in its
`host_vars`). Two drivers on one cmd_vel topic = one joystick drives both robots.

On golem, the gamepad is **js1 → `/golem/joy/joystick1`**. `joystick0` is the
Sipeed NanoKVM's fake USB gamepad — subscribing to it looks right and does nothing.

## Health check (start here)

On the robot (`ssh baldur` / `ssh nepr` — SSH aliases come from the
fleet-portal `connect.sh`, or use the `ansible` user + key from this repo):

```sh
systemctl is-active xdrive-driver
journalctl -u xdrive-driver -n 20 --no-pager
```

A healthy start shows exactly these two lines, a second apart:

```
[xdrive_driver]: opening REV hub on /dev/ttyUSB0; wheel->port map [...]
[xdrive_driver]: xdrive_driver ready
```

ROS-level proof of life — **you MUST set the domain first; SSH sessions default
to domain 0 and see nothing:**

```sh
export ROS_DOMAIN_ID=42
source /opt/ros/kilted/setup.bash
ros2 topic hz /wheel_states        # baldur   → ~10 Hz
ros2 topic hz /nepr/wheel_states   # nepr     → ~10 Hz
```

A steady ~10 Hz stream is real proof (the driver generates it from encoder
reads). Seeing `/rosout` or `/parameter_events` proves **nothing** — the ros2
daemon makes those itself.

## Driving it

**Joystick (baldur):** power the gamepad on golem, put it in ANALOG mode (LED
lit). Left stick = forward/strafe, right stick X = rotate. That's it — both
services are systemd units and start on boot.

**Manual command (any robot) — MOTORS MOVE; put the robot on a stand:**

```sh
export ROS_DOMAIN_ID=42 && source /opt/ros/kilted/setup.bash
# gentle forward on nepr; publishes at 10 Hz, Ctrl-C to stop
ros2 topic pub -r 10 /nepr/cmd_vel geometry_msgs/msg/Twist \
  '{linear: {x: 0.3}}'
```

Stopping the publisher stops the robot: the driver zeroes the motors after
**500 ms** without a command, and the hub itself has a 2.5 s keep-alive
failsafe. There is no way to leave it running away.

**From a non-ROS agent:** the rosbridge gateways (`ws://agony:9090`,
`ws://torture:9090`, `ws://ali:9090`) accept JSON pub/sub with no credentials —
publish the same Twist to the robot's cmd_vel topic via roslibpy/roslib.js
(see [Using the rosbridge Gateway](rosbridge.md)).

## Rules that keep it working (each learned the hard way)

1. **One process per serial port.** Never run `xdrive_driver`, or any rhsp
   script, by hand while the `xdrive-driver` service is active. Two rhsp
   sessions interleave frames on `/dev/ttyUSB0` and both fail (symptom seen
   live: the service crashloops with `rcl node's context is invalid`).
   Check for squatters: `sudo lsof /dev/ttyUSB0` — exactly one `xdrive_dr`
   process owned by `ros` is correct. To experiment manually:
   `sudo systemctl stop xdrive-driver` first, `... start` when done.
2. **`export ROS_DOMAIN_ID=42`** in every SSH session before any `ros2` command.
3. **`ModuleStatus: KeepAliveTimeout|FailSafe` is healthy** on an idle hub — it
   means "parked with motors off because nobody is commanding me", not an error.
4. **The hub enumerates itself.** `serial_port` stays `auto`
   (`rhsp.enumerate_hubs()`); the udev rule also pins `/dev/revhub` as a stable
   alias. Don't hardcode `ttyUSB0`/`ttyUSB1` — the number changes on replug.

## Adding a robot / recalibrating

1. In `inventory/host_vars/<host>.yml`:
   `install_revhub: true`, `xdrive_run_driver: true`,
   `xdrive_cmd_vel_topic: /<host>/cmd_vel`,
   `xdrive_joint_state_topic: /<host>/wheel_states`.
2. `ansible-playbook playbooks/revhub.yml --limit <host>` (library + udev),
   then `ansible-playbook playbooks/xdrive.yml --limit <host>` (build + service).
3. Run the health check above.
4. Calibrate on a stand: sweep motors one port at a time to map
   `xdrive_motor_ports` `[fl,fr,rl,rr]` and flip any reversed wheel in
   `xdrive_wheel_signs`, then raise `xdrive_max_power` — baldur's bench notes
   (in `host_vars/baldur.yml`) found ~12000 needed to break stiction, 16000
   comfortable. Redeploy with the xdrive playbook after each change.

## Where things live

- **Repo** (`ros-deploy`): `roles/revhub` (rhsp lib + `/dev/revhub` udev),
  `roles/xdrive` (build + systemd), `ros_pkgs/xdrive` (driver, joy mapper,
  kinematics + tests), `playbooks/revhub.yml`, `playbooks/xdrive.yml`.
- **On the robot:** overlay workspace `/opt/ros_ws`, unit
  `/etc/systemd/system/xdrive-driver.service` (runs as user `ros`, domain 42
  baked in), hub at `/dev/revhub → ttyUSB*`.
- **On golem:** unit `joy-to-twist.service`; the joy publisher itself comes from
  `roles/joy` (which handles the `SDL_JOYSTICK_DEVICE` env joy_node needs).
