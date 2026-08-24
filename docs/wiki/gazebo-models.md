---
title: Robot Models for the Simulator
blurb: The 31-model collection installed on buzzkill — which ones drive, which need a plugin block, and which are geometry only.
order: 41
updated: 2026-08-23
tags: [ros, gazebo, simulation, models, buzzkill]
---

# Robot Models for the Simulator

`/opt/gazebo/models` on `buzzkill` holds a converted collection of 31 robot and
field models, each a standard Gazebo model directory (`model.config`,
`model.sdf`, `meshes/`) addressable as `model://<name>`. Source:
`RobotProjects/urdf-collection`.

They are filed by how usable each one actually is, because "it loads" and "you
can drive it" turned out to be very different questions:

```
/opt/gazebo/models/
  1-drives/           6   spawn and drive, nothing to edit
  2-needs-plugin/     9   one generated DiffDrive block away from driving
  3-needs-work/       7   missing caster, swerve, unreadable geometry, dead plugins
  4-geometry-only/    8   fields and game pieces (correct), plus 2 dead-plugin robots
```

Model **names** are unaffected by the grouping, so nothing that already
referenced one needs editing:

```xml
<include><uri>model://turtlebot3_burger</uri></include>
```

> **Gazebo scans each directory on `GZ_SIM_RESOURCE_PATH` exactly one level
> deep — it does not recurse.** So `/opt/gazebo/models` alone now resolves
> nothing; every tier directory has to be on the path as well. `roles/gazebo`
> handles this: its profile script adds any subdirectory that is *not* itself a
> model (i.e. has no `model.sdf`), so flat and grouped layouts both work. If
> `model://` stops resolving after you add models, check this first:
>
> ```bash
> echo "$GZ_SIM_RESOURCE_PATH" | tr : '\n'
> ```

## Loading is not the same as driving

**All 30 models load.** That was already true and is still true on Jetty —
`gz_smoke.py` from the collection reports 30/30 on `gz-sim 10.4.0`, matching the
result the collection recorded on Harmonic 8.14.

That number is also the single most misleading thing about the collection. A
model that loads gives you geometry, inertia and collision — it does **not**
give you a robot that responds to `/cmd_vel`. The drivetrain is a `<plugin>`
block, and for most of these robots it was never in the URDF to begin with:
upstream supplies it from a separate `ros2_control` launch file, and converting
a description into a standalone `model.sdf` leaves it behind.

So the useful question is not "does it load" but "does it move when told to".
Measured 2026-08-19 by spawning each model on a ground plane, commanding
`linear.x = 0.6 m/s` for four seconds, and reading the world pose before and
after. **5 of 30 drive as shipped.**

## Tier 1 — drives as shipped (6)

Spawn and go. Nothing to edit.

| Model | Drive system | Travelled | Command topic |
|---|---|---|---|
| `romi` | DiffDrive | 2.13 m | `/cmd_vel` |
| `linorobot2_2wd` | DiffDrive | 2.38 m | `/cmd_vel` |
| `linorobot2_4wd` | DiffDrive | 2.41 m | `/cmd_vel` |
| `linorobot2_vattenkar` | DiffDrive | 2.42 m | `/cmd_vel` |
| `linorobot2_mecanum` | MecanumDrive | 2.35 m | `/cmd_vel` |
| `patribots` | swerve (8 joint controllers + fleet mixer) | 2.06 m | `/sim/swerve/cmd_vel` (ROS) |

> **All of the `/cmd_vel` five listen on the same bare topic.** Spawn two of
> them into the same world and one teleop drives both at once. Give each an
> explicit `<topic>` before putting more than one in a world — this is a
> property of the models, not of the fleet's topic layout.

### `patribots` — the swerve robot (FRC 4738)

The one swerve that graduated from tier 3, and it works differently from the
others: no Gazebo system can mix swerve, so the model carries a position
controller per steer joint and a velocity controller per drive wheel, and
**`buzzkill` runs the mixing as fleet services** (roles/swerve_drive +
roles/gz_bridge, from this repo). Spawn it and it drives:

```bash
ros2 run ros_gz_sim create -world <world> \
  -file /opt/gazebo/models/1-drives/patribots/model.sdf -name patribots -z 0.3
ros2 topic pub -r 10 /sim/swerve/cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.5, y: 0.2}, angular: {z: 0.5}}"   # yes, it strafes
```

The F310 mapping (calibrated with Eric driving, 2026-08-24 — note the model's
documented +x is the intake side, which reads as a strafe on screen, so the
sticks are wired to perception, not to the Twist component names):

| Control | Action |
|---|---|
| left stick Y | drive forward / back |
| left stick X | turn |
| right stick X | strafe |
| right stick Y | hood elevation (aim) |
| D-pad left/right | turret azimuth |
| D-pad down / up | intake scoop deploy / stow |
| RB (hold) | flywheel spin at 200 rad/s, release stops |
| Y | home everything: turret/hood/intake to stow, flywheel stop (also levels the tracked vehicle's flippers) |

Drive is revhub's `motion_control`, mechanisms are `joint_teleop` — the same
node that runs the tracked vehicle's flippers, whose D-pad bindings it shares
(a command to a model that is not spawned goes nowhere; despawn one robot if
both are ever in a world together). Measured end to end 2026-08-23 in the live
desktop world: 2.06 m forward at a commanded 0.5 m/s, 1.44 m strafe, 3.17 rad
spin-in-place. The climber and spindexer have no pad binding yet but are
commandable — bridged `Float64` topics under `/model/patribots/…`, listed in
the model's `UPSTREAM.md`.

Two patribots-specific cautions: the CAD origin is NOT the wheelbase centre
(it sits 0.36 m off; the robot spins about the centre, so the origin sweeping
an arc is correct, not drift), and its 93 MB of undecimated CAD meshes make it
the slowest model in the collection to load.

> `linorobot2_vattenkar`'s SDF declares `<model name='linorobot2_2wd'>`. It is
> a copy-paste error upstream in the collection. Spawning both without
> `-name` gives you a name clash, and `gz model --list` will not tell you which
> is which.

## Tier 2 — drives after one generated plugin block (9)

These have clean left/right wheel joints and no drive plugin at all. Adding a
`DiffDrive` block is mechanical, and the two numbers it needs are already in the
model: wheel separation is the distance between the wheel **joint** origins,
wheel radius is the radius of the wheel link's own collision cylinder. Both were
read off the SDF rather than guessed — a wrong separation makes the robot curve
when told to go straight, and a wrong radius scales every odometry reading.

| Model | Travelled | Separation | Radius | Notes |
|---|---|---|---|---|
| `turtlebot3_burger` | 2.31 m | 0.160 m | 0.033 m | matches the TB3 spec sheet exactly |
| `turtlebot3_waffle` | 2.29 m | 0.288 m | 0.033 m | |
| `turtlebot3_waffle_pi` | 2.36 m | 0.288 m | 0.033 m | |
| `rosbot` | 2.35 m | 0.192 m | 0.043 m | skid-steer, rear pair tied in too |
| `rosbot_xl` | 2.32 m | 0.248 m | 0.048 m | skid-steer, rear pair tied in too |
| `sam_bot` | 2.20 m | 0.360 m | 0.100 m | Nav2 tutorial robot |
| `andino` | 2.22 m | 0.128 m | 0.033 m | |
| `articubot_one` | 2.33 m | 0.297 m | 0.033 m | Classic sensor plugins still dead |
| `romi_ros2_control` | 2.16 m | 0.141 m | 0.035 m | see below |

The block that gets added looks like this — `turtlebot3_burger`'s, verbatim:

```xml
<plugin filename="gz-sim-diff-drive-system" name="gz::sim::systems::DiffDrive">
  <left_joint>wheel_left_joint</left_joint>
  <right_joint>wheel_right_joint</right_joint>
  <wheel_separation>0.160000</wheel_separation>
  <wheel_radius>0.033000</wheel_radius>
  <topic>/model/turtlebot3_burger/cmd_vel</topic>
  <odom_topic>/model/turtlebot3_burger/odometry</odom_topic>
  <frame_id>odom</frame_id>
  <child_frame_id>base_footprint</child_frame_id>
</plugin>
```

On a skid-steer chassis, tie the rear pair in as extra `<left_joint>` /
`<right_joint>` entries. Driving only the front pair leaves the rear wheels
scrubbing and the robot barely moves.

### `romi_ros2_control` is broken for a different reason

Its SDF has an **absolute path from the machine that built the collection**
baked into the `gz_ros2_control` plugin block:

```
/Volumes/Proj/proj/RobotProjects/urdf-collection/.sources/checkout/romi_description/config/romi_controllers.yaml
```

`.sources/` is a gitignored scratch checkout, so that path does not exist on the
build machine either any more, let alone on `buzzkill`. Gazebo does not warn —
the plugin throws `RCLInvalidROSArgsError` and the whole simulator calls
`abort()`. The correct file ships inside the model at
`romi_ros2_control/config/romi_controllers.yaml`; pointing the plugin there
stops the crash. `articubot_one` has the same leaked path.

For simply driving the thing, adding a `DiffDrive` block is much less work than
standing up a controller manager, which is what the 2.16 m above was measured
with.

> `gz_ros2_control` **is** installed here (`ros-lyrical-gz-ros2-control`), but
> its plugin lives in `/opt/ros/lyrical/lib`, which is not on Gazebo's system
> plugin path. Anything using it needs
> `GZ_SIM_SYSTEM_PLUGIN_PATH=/opt/ros/lyrical/lib`. Without it you get "Failed
> to load system plugin", which reads like the package is missing when it is
> not.

## Tier 3 — needs real work (7)

| Model | What is actually wrong |
|---|---|
| `diffbot` | **No caster.** Three links: a base and two wheels, nothing else touching the ground. The base drags and it manages 0.09 m. It is a `ros2_control` teaching example that was never run against physics. Add a caster link. |
| `turtlebot4_standard` | Wheel joint origins are `0 0 0` — the offsets live in nested frames, so separation is not directly readable. Plus 11 Gazebo Classic plugins for the Create 3 base. |
| `turtlebot4_lite` | Same as above. |
| `romi_ros1` | Wheel collision is a mesh, not a cylinder, so no radius to read. ROS 1 Noetic era, Classic laser plugin. |
| `frc_varveropoulos_diff` | Three joints, none of them recognisable as a left/right wheel pair. Needs reading before anything else. |
| `frc_swerve_roboeagles` | Swerve. Four drive joints plus four steering joints; no single Gazebo system does this. The escape route is now proven: `patribots` (tier 1) got per-joint controllers in the model plus the fleet's `twist_to_swerve` mixer, and is the template to copy. |
| `frc_varveropoulos_swerve` | Swerve, same problem. |
| `frc_uwreact_2019` | Its `<mimic>` joints became `gearbox` joints, which DART refuses as a kinematic loop, so the collection dropped them. Needs its own `libfrc_robot_sim.so`, which does not exist for Jetty. |

## Tier 4 — geometry only, and that is correct (8)

`frc_arena_2013`, `ftc_arena_2013`, `ftc_tower`, `ftc_frisbee`, `ftc_red_ring`,
`ftc_blue_ring` are **field elements and game pieces**. They have no drivetrain
because they should not have one. Load them as scenery for a robot to drive
around in — that is the useful thing here, and the FRC/FTC arenas are the reason
to have this collection at all.

`ftc_robot` and `ftc_robot2` are robots, but they actuate through `libFTC.so`
from OSRF's 2013 `ftcsim`, which was never built for anything past Gazebo
Classic. Geometry loads and simulates fine; nothing moves.

## Summary

| Tier | Count | Meaning |
|---|---|---|
| Drives as shipped | 6 | spawn and drive |
| One generated plugin block | 9 | mechanical, parameters readable from the model |
| Needs real work | 7 | missing caster, swerve, unreadable geometry, dead plugins |
| Geometry only (correct) | 8 | fields, game pieces, and two Classic-era FTC robots |

**15 of 31 can be driven today**, 9 of them after an edit that takes a minute.

## See also

- **[Gazebo Simulation](gazebo.md)** — the sim host itself, and
  [how to send it a model](gazebo.md#sending-buzzkill-a-new-robot).
