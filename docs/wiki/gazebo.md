---
title: Gazebo Simulation
blurb: Run simulated robots on the real fleet's DDS domain — where the simulator lives, how to drive it, and why rendering is the thing that bites.
order: 40
tags: [ros, gazebo, simulation, buzzkill]
---

# Gazebo Simulation

Gazebo runs a physics-and-sensors model of a robot that publishes and subscribes on
the **same ROS 2 topics the real robot uses**. Because the simulation host sits on
the fleet's DDS domain (`42`), a node does not know or care whether the `/cmd_vel`
it publishes reaches a REV Hub or a simulated drivetrain — which is the whole point.
Develop against the sim, deploy to the robot unchanged.

## Where it is

| Thing | Value | Notes |
|-------|-------|-------|
| **Sim host** | `buzzkill` (`192.168.1.23`) | amd64 Ubuntu **26.04**, 16 cores, 25 GB RAM, AMD Radeon 680M. |
| **ROS release** | **Lyrical Luth** | Not Kilted like the rest of the fleet — see [the split](#the-fleet-is-split-across-two-ros-releases). |
| **Gazebo release** | **Jetty** (`gz-sim 10`) | Pinned to Lyrical. Not a free choice — see [below](#why-the-gazebo-version-is-not-a-choice). |
| **Bridge** | `ros_gz` | `ros_gz_bridge`, `ros_gz_sim`, `ros_gz_image`, `ros_gz_interfaces`. |
| **DDS domain** | `42` | Same domain as every real node, so sim and hardware are interchangeable. |
| **Desktop** | XFCE + TurboVNC on `:1` | `vnc://buzzkill:5901`, session owned by the shared `ros` account. |
| **Rendering** | AMD Radeon 680M via VirtualGL | Session runs under `vglrun` (EGL back end) — see [Rendering](#rendering-gpu-and-how-to-tell). |
| **rosbridge** | `ws://buzzkill:9090` | Also a [rosbridge gateway](rosbridge.md), so non-ROS clients can watch the sim. |
| **Shared worlds/models** | `/opt/gazebo/worlds`, `/opt/gazebo/models` | On `GZ_SIM_RESOURCE_PATH`; group-writable by `ros`. |

Deploy or re-deploy it with:

```bash
ansible-playbook playbooks/gazebo.yml --limit buzzkill
```

## Running a simulation

Connect to the desktop (`vnc://buzzkill:5901`), open a terminal — you land in the
shared `ros` account with ROS 2 and the Gazebo environment already sourced — and:

```bash
gz sim shapes.sdf          # built-in demo world, proves the install
gz sim -v4 my_world.sdf    # anything in /opt/gazebo/worlds resolves by name
```

Nothing is bridged to ROS yet. `gz sim` speaks *Gazebo transport*, which is a
separate bus from DDS; the bridge is what joins them, one topic at a time:

```bash
# Gazebo topic  <-  ->  ROS topic, with both type names given
ros2 run ros_gz_bridge parameter_bridge \
  /cmd_vel@geometry_msgs/msg/Twist@gz.msgs.Twist \
  /odom@nav_msgs/msg/Odometry@gz.msgs.Odometry
```

Then, from **any** node on the fleet, the simulated robot is just another node:

```bash
ros2 topic list | grep cmd_vel
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.2}}"
```

## Sending buzzkill a new robot

You do not need to sit at the VNC desktop to put a robot in the simulator, and
you do not need to restart the simulator to add one. Copy the model over, then
spawn it into the world that is already running.

### 1. Copy the model over

A Gazebo model is a directory — `model.config`, `model.sdf`, `meshes/`. Drop it
in `/opt/gazebo/models` and it becomes addressable everywhere as
`model://<name>`, because that directory is on `GZ_SIM_RESOURCE_PATH`:

```bash
# 43 MB of models moves in a few seconds on the LAN
tar czf - my_robot | ssh ros@buzzkill 'tar xzf - -C /opt/gazebo/models'
```

`/opt/gazebo/models` is grouped into tier directories (`1-drives`,
`2-needs-plugin`, …) — drop a new model straight into `/opt/gazebo/models` and
it still resolves, because that directory is on the path too. Filing it under a
tier is a judgement about the model, not a requirement.

`scp -r my_robot ros@buzzkill:/opt/gazebo/models/` does the same thing and is
easier to remember; `tar` is worth it once the meshes get big. The directory is
group-owned by `ros` and group-writable, so the shared account needs **no
sudo** — see [connecting](fleet-portal.md) for the credentials.

> **Copying from a Mac, use `tar --disable-copyfile` or `COPYFILE_DISABLE=1`.**
> Otherwise macOS writes an AppleDouble `._model.config` beside every real file,
> and Gazebo's model-database scan tries to parse them as models.

Models already installed on `buzzkill` are catalogued in
[Robot Models for the Simulator](gazebo-models.md) — start there, because most
of them load without being drivable and the page says which is which.

### 2. Spawn it into the running world

`ros_gz_sim create` injects a model into a live simulation. The robot appears
immediately; nothing restarts, and anything already running keeps running:

```bash
ssh ros@buzzkill
ros2 run ros_gz_sim create \
  -world fleet \
  -file /opt/gazebo/models/1-drives/romi/model.sdf \
  -name romi -x 0 -y 0 -z 0.15
# [INFO] [ros_gz_sim]: Entity creation successful.
```

> **Pick the model from `1-drives`.** Anything in `2-needs-plugin` spawns just
> as happily and then sits there for good — it has no drivetrain, so the command
> topic below does not exist and there is nothing to bridge. See
> [Robot Models for the Simulator](gazebo-models.md).

> **Do not wrap the include in an outer `<model>`.** Spawning a file that nests
> `<include><uri>model://romi</uri></include>` inside another `<model>` re-scopes
> the drive plugin's topic, so the robot arrives and is then unreachable.
> Spawn the model's own `model.sdf`.

`-world` is the name **inside** the world SDF, not the filename —
`fleet_diff_drive.sdf` contains `<world name="diff_drive">` and
`fleet_tracked_vehicle.sdf` contains `<world name="default">`. Getting it wrong
fails silently-ish: the service simply is not there to call.

`-file` takes any path, so a one-off model does not have to be installed at all.
There is also `-string` (SDF as text, no file anywhere) and `-topic`. Confirm
what landed with `gz model --list`.

> **The world must include the `UserCommands` system**, or `/world/<name>/create`
> does not exist and there is nothing to spawn into. `/opt/gazebo/worlds/fleet_empty.sdf`
> is a bare ground-plane world that has it, for exactly this.

### 3. Drive it

> **`buzzkill` now runs a persistent bridge** — `gz-bridge.service`
> (roles/gz_bridge) carries the fleet's standard sim topics across the ROS/gz
> boundary at boot: every `/model/patribots/...` command topic, the tracked
> vehicle's four flipper topics, and patribots odometry back out. If your topic
> is on that list (see `gz_bridge_topics` in `host_vars/buzzkill.yml`), skip
> the manual bridge below — it is already running. The hand-run bridge remains
> the right tool for ad-hoc topics like a freshly spawned romi's `/cmd_vel`.
>
> The **patribots swerve robot** is the fully wired example: spawn it and it
> drives from `/sim/swerve/cmd_vel` (left stick + right-stick strafe on the
> F310) with no further setup — the chain is
> `motion-control.service -> swerve-drive.service -> gz-bridge.service`.
> See [Robot Models for the Simulator](gazebo-models.md#patribots--the-swerve-robot-frc-4738).

Bridge the model's command topic into ROS, then it is an ordinary fleet node:

```bash
# romi's DiffDrive plugin declares <topic>/cmd_vel</topic>, so that is the name
# to bridge. ] = ROS -> Gazebo only; a bidirectional @ makes the bridge a second
# publisher on the topic and echoes your commands back at you.
ros2 run ros_gz_bridge parameter_bridge \
  /cmd_vel@geometry_msgs/msg/Twist]gz.msgs.Twist &

ros2 topic pub -r 10 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.5}}"
```

Measured end to end on 2026-08-19 — copied in, spawned into a running world,
driven from ROS — the robot went from `x = 0.0` to `x = 3.57` in eight seconds
at a commanded 0.5 m/s.

Note that `diffdrive-teleop.service` also publishes on `/cmd_vel` (that is the
joystick path). With the stick centred the two coexist, but a deflected stick
and a scripted `topic pub` will fight. Remap the bridge's ROS side if you want
scripted driving to have the topic to itself:

```bash
ros2 run ros_gz_bridge parameter_bridge \
  /cmd_vel@geometry_msgs/msg/Twist]gz.msgs.Twist \
  --ros-args -r /cmd_vel:=/sim/cmd_vel      # then publish to /sim/cmd_vel
```

### `gz sim` is a wrapper — check for stale servers first

**This is the one that will waste your afternoon.** `gz sim` is a launcher; the
process that actually runs the world is `gz-sim-main`. So this kills nothing:

```bash
pkill -f "gz sim"        # matches the wrapper, not the server. Useless.
```

Every "restart", the old server survives and a new one joins it. They all
advertise the *same* gz-transport topics and services, so `gz model --list`,
pose queries and spawns are answered by whichever replies first — spawns land in
one world while you read poses from another. It presents as a robot that spawns
successfully and then will not move, or a `-name` that comes back different.

Count them before believing anything:

```bash
pgrep -af gz-sim-main
```

Kill by the real binary, and narrow the pattern to your own world so you do not
take out someone else's session:

```bash
pkill -f 'gz-sim-main.*fleet_empty'
```

Two more traps in the same family: over SSH, `pkill -f "gz sim"` also matches the
SSH shell's own command line and kills your session before it reaches Gazebo —
put the pattern in a script file instead. And a Python `Popen(["gz","sim",...])`
*is* safe to `terminate()`, because the wrapper execs and the PID you hold is
the real server.

The command topic is whatever the model's drive plugin declares, and it varies:
several models in the collection listen on bare `/cmd_vel`, which means two of
them in one world are driven by a single teleop. Check before you spawn a
second robot:

```bash
gz topic -l | grep cmd_vel
```

### Watching it

The sim above runs headless (`gz sim -s`). To actually see it, connect to
`vnc://buzzkill:5901` and attach the GUI to the running server:

```bash
gz sim -g          # GUI only, joins the server already running
```

Started this way the GUI inherits the desktop's VirtualGL acceleration, which is
the whole reason the box has a GPU — see [Rendering](#rendering-gpu-and-how-to-tell).

## Rendering: GPU, and how to tell

`buzzkill` renders on its **AMD Radeon 680M**, and that is not automatic — it is
configured, and it is worth understanding because the failure mode is silent.

A VNC session's X server has no graphics card behind it, so Mesa falls back to
**llvmpipe** and renders on the CPU. Gazebo still starts and still looks correct.
You only notice because heavy scenes crawl. Measured here with glmark2 at 1280x720:

| scene | llvmpipe (CPU) | Radeon 680M | |
|-------|---------------|-------------|---|
| terrain | 20 fps | **226 fps** | ~11x |
| refract | 40 fps | **418 fps** | ~10x |
| shading (phong) | 532 fps | 933 fps | 1.8x |
| bump (light scene) | 1239 fps | 905 fps | **0.7x** |

Note the last row: on a trivially light scene, software is *faster*, because
VirtualGL's cost is reading rendered pixels back to the VNC server. That cost is
fixed, so the heavier and more realistic the world, the more the GPU wins — and a
real Gazebo world with meshes, shadows and textures is firmly in the ~10x band.

### How it is set up

`desktop_vnc_virtualgl: true` makes TurboVNC start the session with `-vgl`, which
runs the **window manager** under `vglrun`. Every app launched from the desktop
inherits VirtualGL through `LD_PRELOAD`, so acceleration is the default rather than
something you must remember — including Gazebo started indirectly, e.g.
`ros2 launch ros_gz_sim gz_sim.launch.py`.

The back end is VirtualGL's **EGL** back end (`VGL_DISPLAY=egl0`), rendering
straight on the DRM render node `/dev/dri/renderD128`. That choice is what keeps
this unattended: the older GLX back end needs `vglserver_config` plus a
display-manager restart. EGL needs neither — only that the session user is in the
`render` group, which `roles/gazebo` handles.

The back end is pinned in `/etc/turbovncserver.conf` as
`$vglrun = "vglrun -d egl0 +wm"` rather than left to `VGL_DISPLAY`, because that
variable does not reliably survive `vncserver` -> `xstartup` -> window manager. When
it doesn't, VirtualGL silently tries the GLX default and you get
`[VGL] ERROR: Could not open display :0`.

### Checking it

```bash
DISPLAY=:1 glxinfo -B | grep -i renderer
```

Careful reading this: run from an SSH session it reports **llvmpipe**, and that is
*not* a fault — your SSH shell is not a child of the accelerated window manager. It
only proves something is wrong if a terminal **inside the VNC desktop** says
llvmpipe. The deploy prints both numbers ("unaccelerated" and "via VirtualGL") so
the distinction is explicit.

To force acceleration for one command from a context that lacks it (plain SSH with
X11 forwarding, a systemd unit):

```bash
gz-gpu sim shapes.sdf        # == vglrun gz sim shapes.sdf
```

If the render engine fails to start at all, `gz sim --render-engine ogre` falls back
to the older engine, which asks less of the GL stack than the default `ogre2`.

## Driving it with a joystick

Plug a gamepad into the sim host and drive the simulated robot with it. Nothing
about the chain is simulation-specific — the same `/cmd_vel` reaches a real robot:

```
pad -> roles/joy (joy_linux_node)        -> /buzzkill/joy/joystick0
    -> diffdrive_teleop joy_to_twist     -> /cmd_vel
    -> ros_gz_bridge                     -> Gazebo
```

Single-stick "arcade" on the left stick: vertical drives, horizontal turns.

**Axis signs are not portable — measure them.** The same Logitech F310 reports a
different layout depending on the X/D switch on its back, and the pad on
`buzzkill` reports **positive** for up/left, the opposite of the usual Linux
convention. Assuming the convention inverted *both* controls at once, which
presents as two unrelated faults ("forward goes backward" and "left turns
right") but is one. Read the real values off the hardware:

```bash
ros2 run diffdrive_teleop joy_probe --ros-args -p joy_topic:=/buzzkill/joy/joystick0
```

Push one control at a time; it prints the axis index and sign to put in
`diffdrive_teleop_axis_*` / `diffdrive_teleop_invert_*`. It also warns when an
axis rests at full deflection — those are the analog **triggers**, and binding
drive or turn to one makes the robot take off the instant the node starts.

### Flippers on the tracked vehicle

`tracked_vehicle_simple.sdf` has four articulated flippers, but in the **stock**
world they cannot move: the joints are welded (`<lower>0</lower><upper>0</upper>`)
and there is no joint controller — they exist as rigid contact geometry for the
tracked-vehicle plugin. `roles/gazebo` therefore derives
`/opt/gazebo/worlds/fleet_tracked_vehicle.sdf` with the joints unlocked
(+/-1.2 rad) and a `JointPositionController` on each, exposing:

```
/model/simple_tracked/flipper/{front_left,front_right,rear_left,rear_right}/cmd_pos
```

`joint_teleop` drives those from the pad. Control is **rate-based** — hold to
move, release and the flipper stays put — because mapping an axis straight to an
angle would snap the joints somewhere the instant the node starts, and would
send a resting-at-full-deflection axis (an analog trigger) straight to its limit.

| control | effect |
|---------|--------|
| D-pad up / down | both FRONT flippers |
| D-pad left / right | both REAR flippers |
| button Y | return all four to level |

The D-pad is used rather than the triggers on purpose: axes 2 and 5 on an F310
are the analog triggers and rest at full deflection.

Bridge the flipper topics one-way alongside `cmd_vel`:

```
/model/simple_tracked/flipper/front_left/cmd_pos@std_msgs/msg/Float64]gz.msgs.Double
```

### Speed lives in the world, not the teleop

Stock `diff_drive.sdf` clamps inside the DiffDrive plugin at
`max_linear_velocity 0.5`. Commanding 1, 2 and 3 m/s all measured the same
~0.64 m/s, so raising the teleop's `max_linear` alone does nothing.
`roles/gazebo` therefore installs a tuned copy at
`/opt/gazebo/worlds/fleet_diff_drive.sdf` with the caps raised, which makes
`diffdrive_teleop_max_linear` the real limit (measured: commanded 2.0 -> 2.10 m/s).

### Bridge direction matters

Bridge `/cmd_vel` **one-way** into Gazebo:

```
/model/vehicle_blue/cmd_vel@geometry_msgs/msg/Twist]gz.msgs.Twist    # ] = ROS -> GZ
/model/vehicle_blue/odometry@nav_msgs/msg/Odometry[gz.msgs.Odometry  # [ = GZ -> ROS
```

`@` on both sides is **bidirectional**, which makes the bridge a second
publisher on `/cmd_vel`: Gazebo echoes commands back, they get re-sent, and
stale commands can outlive the stick being released. Check with
`ros2 topic info /cmd_vel -v` — publisher count should be 1.

## The fleet is split across two ROS releases

`buzzkill` runs **Lyrical Luth**; the Pis and robots run **Kilted**. That is not a
choice anyone made — packages.ros.org builds exactly one ROS distro per Ubuntu
suite:

| Ubuntu suite | ROS distro available | Runs on |
|--------------|----------------------|---------|
| `noble` (24.04) | `ros-kilted-*` only | every Pi and robot |
| `resolute` (26.04) | `ros-lyrical-*` only | `buzzkill` |

There is no Kilted build for 26.04, and no Lyrical build for 24.04. So the sim host
either runs a different ROS release from the robots, or the box gets reimaged to
24.04.

**What this costs you:** ROS 2 [does not guarantee communication between
distributions](https://github.com/ros2/ros2_documentation/issues/3288). In practice
stock message types do interoperate, because their definitions haven't changed —
**measured 2026-08-15**, publishing from `buzzkill` (Lyrical) and receiving on
`agony` (Kilted), domain 42:

```
buzzkill$ ros2 topic pub -r 2 /sim_interop_test std_msgs/msg/String "{data: hello-from-lyrical}"
agony$    ros2 topic echo /sim_interop_test --once
data: hello-from-lyrical          # payload intact, not just discovery
```

But a message whose definition *did* change between Kilted and Lyrical will simply
**fail to match**: no error, no warning, the subscription just never fires. Note
that a topic appearing in `ros2 topic list` is **not** evidence of this working —
listings survive their publisher (the same trap as domain-42 verification). Only an
`echo` that returns a payload proves the path.

So when a sim topic goes quiet, check this before anything else — from both sides:

```bash
ssh ros@buzzkill 'ros2 topic echo /cmd_vel --once'   # Lyrical side
ssh ros@baldur   'ros2 topic echo /cmd_vel --once'   # Kilted side
```

If the publisher is visible on one side and not the other, it's the distro split,
not your code. Kilted reaches end-of-life in **November 2026**, so the resolution is
to move the fleet forward to Lyrical — not to drag `buzzkill` back.

## Why the Gazebo version is not a choice

ROS releases and Gazebo releases are paired by [REP-2000](https://ros.org/reps/rep-2000.html),
and `roles/gazebo` derives one from the other (`gazebo_release_by_ros`) rather than
letting you set it:

| ROS 2 | Ubuntu | Gazebo |
|-------|--------|--------|
| Humble | 22.04 | Fortress |
| Kilted | 24.04 | Ionic |
| Lyrical | 26.04 | **Jetty** (`gz-sim 10`) |

Everything is installed from **packages.ros.org as vendor packages**
(`ros-lyrical-gz-sim-vendor`, `ros-lyrical-gz-tools-vendor`, `ros-lyrical-ros-gz`),
which carry the exact Gazebo build the ROS release was tested against.

> **Do not add the `packages.osrfoundation.org` repo and `apt install gz-jetty` on
> top.** That puts a second, separately-built set of `gz` libraries on the system;
> what you get is not a newer Gazebo but two of them, and the failures show up at
> run time as symbol and plugin-loading errors rather than at install time.

## See also

- **[Robot Models for the Simulator](gazebo-models.md)** — the 30 models installed
  on `buzzkill`, triaged by whether they actually drive.
- **[Using the rosbridge Gateway](rosbridge.md)** — `buzzkill` is one; watch sim topics from a browser.
- **[Setting Up a New Node](new-node.md)** — how `buzzkill` was onboarded.
- [Gazebo Ionic documentation](https://gazebosim.org/docs/ionic/) and
  [`ros_gz`](https://github.com/gazebosim/ros_gz) upstream.
