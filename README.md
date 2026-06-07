# ros-deploy

Ansible project for deploying ROS 2 (Robot Operating System) to remote
machines — physical hardware, Raspberry Pis, VMs, and Docker containers —
across Intel (amd64) and ARM64 architectures.

SSH credentials and secrets are managed with
[dotconfig](https://github.com/ericbusboom/dotconfig).

## Features

| Capability | Details |
|---|---|
| ROS 2 (Humble) | Ubuntu 22.04, amd64 + arm64 |
| ROS 2 (Kilted) | Ubuntu 24.04, amd64 + arm64 |
| Raspberry Pi | 64-bit Ubuntu (arm64); detected automatically |
| Docker | ROS runs in a container with `--network host` so it joins the same ROS network as physical nodes |
| X Windows | SSH X11 forwarding configured so remote GUI apps render on your local screen |
| Mac (run a node) | ROS node on macOS via a UTM **bridged** Ubuntu 24.04 VM (`macvm`) — joins the fleet like a physical node. See "ROS on a Mac (UTM)" |
| Multi-arch | Multi-arch Docker images via `buildx`; QEMU binfmt support on Linux hosts |
| Network discovery | All nodes — bare-metal, VM, or container — share the same `ROS_DOMAIN_ID` and discover each other via DDS |
| Ansible service account | Dedicated `ansible` OS user with its SSH key managed by dotconfig |

---

## Repository layout

```
ros-deploy/
├── ansible.cfg                  # Ansible settings (remote_user=ansible)
├── inventory/
│   ├── hosts.ini                # EDIT THIS — host IPs for your network
│   ├── group_vars/
│   │   ├── all.yml              # Variables for every host
│   │   └── ros_docker_hosts.yml # Variables for Docker hosts
│   └── host_vars/
│       ├── raspi1.yml           # Raspberry Pi example
│       └── docker-host1.yml     # Docker host example
├── playbooks/
│   ├── bootstrap.yml            # ONE-TIME: create ansible user (run first)
│   ├── site.yml                 # Run everything
│   ├── ros_install.yml          # ROS 2 on bare-metal / VMs only
│   ├── xwindows.yml             # X11 forwarding only
│   ├── docker_ros.yml           # Docker + ROS container only
│   └── heartbeat.yml            # Build the connectivity-check package
├── roles/
│   ├── ansible_user/            # Creates ansible OS user + deploys SSH key
│   ├── common/                  # Baseline packages, /etc/hosts entries
│   ├── ros/                     # ROS 2 install (Humble / Kilted)
│   ├── xwindows/                # SSH X11 forwarding, optional Xvfb
│   ├── docker/                  # Docker CE install (multi-arch)
│   ├── ros_docker/              # docker-compose + systemd service for ROS
│   └── heartbeat/               # Builds the connectivity-check ROS package
├── ros_pkgs/
│   └── heartbeat/               # ROS 2 package: announces host, lists peers
├── scripts/
│   ├── bootstrap-ansible-user.sh  # First-run helper (see Quick start)
│   └── set-static-ip.sh           # Configure a static IP on a remote host
└── docker/
   ├── humble/                  # Multi-arch Dockerfile for ROS 2 Humble
   └── kilted/                  # Multi-arch Dockerfile for ROS 2 Kilted
```

---

## Quick start

### 0 — Prerequisites (control machine)

```bash
pip install ansible
ansible-galaxy collection install community.general community.docker ansible.posix
pipx install dotconfig          # SSH key + secrets management
# Install SOPS: https://github.com/getsops/sops
# Generate an age key if you don't have one:
age-keygen -o ~/.config/sops/age/keys.txt
```

### 1 — Edit the inventory

```bash
$EDITOR inventory/hosts.ini
```

Replace the example IPs with your machines.  The `ansible_user` is set
globally to `ansible` in `ansible.cfg` — do **not** add per-host
`ansible_user=` entries (that is handled by the bootstrap step below).

Set `ros_version` in `inventory/group_vars/all.yml`:

| Value    | ROS release              | Ubuntu |
|----------|--------------------------|--------|
| `humble` | ROS 2 Humble Hawksbill   | 22.04  |
| `kilted` | ROS 2 Kilted Kaiju       | 24.04  |

### 2 — Set up the Ansible SSH key

```bash
# Generate the keypair once (stored encrypted in config/keys/):
dotconfig key gen ansible

# Decrypt it for this session (repeat every new shell):
dotconfig key load ansible        # → config/files/ansible (mode 0600)
```

### 3 — Set a static IP (optional)

Use the helper script to assign a static IP to a freshly provisioned host:

```bash
./scripts/set-static-ip.sh -u <your_user> -i 192.168.1.11 <hostname>
```

### 4 — Bootstrap each new host (one time per host)

This uses **your personal** SSH account to create the `ansible` service
user and install its public key.  After this step all Ansible runs use
the `ansible` account automatically.

```bash
# Single host:
./scripts/bootstrap-ansible-user.sh -u eric -K agony.local

# Or run the bootstrap playbook directly:
ansible-playbook playbooks/bootstrap.yml -u <your_user> --ask-become-pass
```

### 5 — Run the full deployment

```bash
ansible-playbook playbooks/site.yml
```

Or target individual playbooks:

```bash
ansible-playbook playbooks/ros_install.yml   # ROS 2 only
ansible-playbook playbooks/docker_ros.yml    # Docker + ROS container
ansible-playbook playbooks/xwindows.yml      # X11 forwarding
ansible-playbook playbooks/heartbeat.yml     # Build connectivity-check package
```

### 6 — Verify ROS 2 networking

```bash
ssh ubuntu@192.168.1.11
ros2 topic list        # should list /parameter_events etc.
ros2 node list
```

#### Heartbeat connectivity check

The `heartbeat` package (built by `site.yml` or `heartbeat.yml`) provides a
single node that publishes this host's name on `/heartbeat` once per second
and prints the set of peers it sees on the same topic. Run it on every node
to confirm DDS discovery is working across the whole fleet:

```bash
ssh ubuntu@192.168.1.11
ros2 run heartbeat heartbeat
# [INFO] heartbeat up on host 'agony', publishing to /heartbeat every 1s
# [INFO] I see 2 peer(s): docker-host1, raspi1
```

A peer drops off the list after ~10 s of silence. `Ctrl-C` to stop.
For nodes running inside a Docker container, exec into the container first:
`docker exec -it ros-docker ros2 run heartbeat heartbeat` (the package
must be present in the container image, or mount the host workspace into it).

---

## SSH key management (dotconfig)

The ansible SSH keypair is stored in dotconfig:

```
config/keys/ansible       ← SOPS-encrypted private key (safe to commit)
config/keys/ansible.pub   ← plaintext public key (safe to commit)
config/files/ansible      ← decrypted private key (gitignored, session-only)
```

Common commands:

```bash
dotconfig key gen ansible              # generate (once, team-wide)
dotconfig key load ansible             # decrypt for this shell session
dotconfig key pub ansible              # print the public key
dotconfig key send ansible USER@HOST   # push public key via ssh-copy-id
```

`ansible.cfg` is pre-configured to use `config/files/ansible` as the
private key and `ansible` as the remote user, so no extra flags are
needed when running playbooks.

Add `config/files/` to `.gitignore` — it holds plaintext private keys.

---

## X Windows forwarding

To display remote GUI applications on your local screen:

```bash
# Trusted X forwarding (use -Y for tools like rviz that need it)
ssh -Y ubuntu@192.168.1.11
# Then launch any GUI:
rviz2 &
rqt &
xclock &
```

The `xwindows` role enables `X11Forwarding yes` in sshd and installs
`xauth` and `x11-apps`.  You may need `XQuartz` installed locally on macOS.

### Headless virtual framebuffer (Xvfb)

Some CI / automated scenarios need a virtual display.  Enable it with:

```bash
ansible-playbook playbooks/xwindows.yml -e xwindows_install_xvfb=true
```

This installs Xvfb and creates a systemd service on `:99`.  Set
`DISPLAY=:99` in your scripts to use it.

---

## Fleet WiFi

ROS 2 DDS discovery uses **multicast**, which does **not** bridge between the
site's separate WiFi SSIDs/APs even though they share the `192.168.1.0/21`
subnet. A node on the wrong SSID is unicast-reachable (ping/SSH work) but
**invisible to DDS discovery** — it sees only its own topics and the fleet can't
see it. So every ROS Pi must be on the same WiFi as the wired hosts: **Busboom
Mesh**.

The `wifi` role (enabled for `raspberry_pis` via `configure_wifi: true`) ensures
each Pi has a NetworkManager profile for Busboom Mesh with its **static**
inventory IP (so it stays reachable at the same address after switching APs),
and a high autoconnect priority. It only switches APs if the host isn't already
on the target connection, and does so detached so the brief drop doesn't abort
the run.

The PSK is **not** stored in the repo. It lives in the SOPS-encrypted dotconfig
secrets as `WIFI_MESH_PSK`; export it into the environment before running:

```bash
dotconfig load dev            # or your deploy; decrypts secrets into .env
set -a; source .env; set +a   # exports WIFI_MESH_PSK
ansible-playbook playbooks/wifi.yml --limit raspberry_pis
```

To change/rotate the PSK: `dotconfig load <deploy>`, edit `WIFI_MESH_PSK` in
`.env`, then `dotconfig save` (re-encrypts `config/<deploy>/secrets.env`).

> Diagnosing isolation: on the suspect node `ros2 topic list` shows only its own
> topics; `iwgetid -r` reveals the wrong SSID; `ros2 multicast send`/`receive`
> between it and a wired host fails one-way while `ping` succeeds.

---

## Cameras

The `cameras` role installs [`camera_ros`](https://github.com/christianrauch/camera_ros)
(libcamera-based) plus image-transport (incl. compressed) and runs one
`camera_node` per camera, publishing on the shared `ROS_DOMAIN_ID` so any
node — including the Mac — can view the streams.

Enable per host in `inventory/host_vars/<host>.yml`:

```yaml
install_cameras: true
cameras_build_rpi_libcamera: true   # Raspberry Pi 5 / CSI cameras (see note)
cameras_list:
  - { namespace: camera0, camera: 0, width: 1280, height: 720, frame_id: camera0_optical_frame }
  - { namespace: camera1, camera: 1, width: 1280, height: 720, frame_id: camera1_optical_frame }
```

Deploy: `ansible-playbook playbooks/cameras.yml --limit <host>` (also part of
`site.yml`). A `ros-cameras` systemd service runs the cameras on boot.
Published topics per camera: `/<ns>/camera/image_raw`,
`/<ns>/camera/image_raw/compressed`, `/<ns>/camera/camera_info`.

### Raspberry Pi 5 note (important)

`camera_ros` bundles **upstream** libcamera, whose PiSP pipeline cannot acquire
the Raspberry Pi downstream kernel's CFE on Ubuntu (`Unable to acquire a CFE
instance` → "no cameras available"). With `cameras_build_rpi_libcamera: true`
the role builds the **Raspberry Pi libcamera fork** (+ `libpisp`, pinned to a
0.5.x tag matching `camera_ros`'s `libcamera.so.0.5`) into `/opt/rpi-libcamera`
and makes `camera_ros` load it via `LD_LIBRARY_PATH` (in the systemd unit and a
`/etc/profile.d/zz-ros-cameras.sh` snippet). The first build compiles libcamera
on the Pi (~minutes); subsequent runs skip it. `vidar` (Pi 5, two IMX296
cameras) uses this.

### Viewing cameras on your Mac (recommended: web_video_server)

The most reliable way to view cameras on a Mac is **`web_video_server`** — it
serves the camera topics as MJPEG over HTTP, so you watch them in a **browser**
(no X11, no XQuartz). Enable it on the camera host:

```yaml
# inventory/host_vars/<host>.yml
cameras_web_video_server: true        # serves on :8080 (cameras_web_video_port)
```

Then open in your Mac browser:

```
http://<host>:8080/                                                # index of streams
http://<host>:8080/stream_viewer?topic=/camera0/camera/image_color # live view (colour-corrected)
```

> **Colour (R/B swap):** camera_ros on Pi 5 publishes image pixels with red and
> blue swapped relative to the encoding label, so `image_raw` shows red as blue.
> With `cameras_color_relay: true` a relay republishes each camera as
> `…/image_color` with the channels corrected (subscribes BEST_EFFORT, publishes
> RELIABLE). **View the `image_color` topics.** The relay starts before
> web_video_server so the topics are discovered at startup.

> Note: `web-video-server` is `PartOf` the camera service, so restarting the
> cameras restarts it too (it must re-discover publishers).

> **Throughput / QoS:** two cameras publishing raw at 1280×720/30 collapse to
> <1 Hz on a Pi 5; 640×480 holds a steady 30 Hz on both. `camera_ros` also
> publishes images **RELIABLE, depth 1** (and ignores `qos_overrides`), so a
> stalled or abandoned subscriber can block the publisher and stall capture.
> The rate-aware watchdog (`cameras_watchdog`, restart below
> `cameras_watchdog_min_rate` Hz) recovers from that automatically. For higher
> resolution, drop the frame rate or run a single camera.

### Viewing cameras on your Mac (via X11 to XQuartz)

> ⚠️ rqt over XQuartz is unreliable: Qt5 frequently renders an all-black window
> over SSH X11 forwarding to XQuartz (no MIT-SHM / no usable GLX). Prefer
> `web_video_server` above. The X11 path below works for simpler X apps.


Run the viewer on any ROS node on the LAN (it subscribes to the camera topics —
no capture needed) and forward its window to your Mac's XQuartz over SSH X11:

```bash
./scripts/view-cameras.sh                              # rqt_image_view on agony (pick topic)
./scripts/view-cameras.sh -t /camera0/camera/image_raw # view camera0 directly (image_view)
./scripts/view-cameras.sh -H torture -u eric           # different node / login user
```

`view-cameras.sh` ensures XQuartz is running, resolves the node's IP from the
inventory, and `ssh -Y`'s in to launch the viewer. The node needs the viewer
packages (`ros-<distro>-rqt-image-view`, `ros-<distro>-image-view`,
`ros-<distro>-image-transport-plugins`, `ros-<distro>-compressed-image-transport`)
— included in the `desktop` ROS variant, or install them directly. Choose
`/camera0/camera/image_raw/compressed` (compressed = network-friendly).

Alternatively, on a node with a local display just run
`ros2 run rqt_image_view rqt_image_view` directly.

---

## rosbridge gateway (non-ROS clients)

The `rosbridge` role runs [`rosbridge_server`](https://github.com/RobotWebTools/rosbridge_suite)
— a JSON-over-WebSocket gateway that lets clients **without any ROS install**
publish and subscribe to ROS 2 topics. The bridge runs on a ROS node (it sees
the fleet's topics over domain 42) and speaks the documented rosbridge v2 JSON
protocol outward on a single port. This also sidesteps the Humble/Kilted split:
clients aren't ROS at all.

Enable it per host (currently `agony`) in `inventory/host_vars/<host>.yml`:

```yaml
install_rosbridge: true
# rosbridge_port: 9090       # default
# rosbridge_address: 0.0.0.0 # default — bind to a LAN IP to restrict reach
```

Deploy: `ansible-playbook playbooks/rosbridge.yml --limit agony` (also part of
`site.yml`). A `rosbridge` systemd service runs the bridge on boot at
`ws://<host>:<port>` (default `ws://agony:9090`).

### Talking to it

**Python** — `pip install roslibpy` (pure Python, no ROS):

```python
import roslibpy
ros = roslibpy.Ros(host='agony', port=9090); ros.run()

# read: ROS → client
roslibpy.Topic(ros, '/agony/joy/0', 'sensor_msgs/Joy').subscribe(
    lambda m: print(m['axes']))

# write: client → ROS
cmd = roslibpy.Topic(ros, '/cmd_vel', 'geometry_msgs/Twist')
cmd.publish(roslibpy.Message({'linear': {'x': 0.5}, 'angular': {'z': 0.2}}))
```

**Browser** — the same with [`roslib.js`](https://github.com/RobotWebTools/roslibjs)
over `ws://agony:9090` (`new ROSLIB.Topic(...).subscribe(...)` / `.publish(...)`).

> ⚠️ **No authentication.** Any client that connects can pub/sub any topic and
> call any service. Fine inside the lab; if untrusted clients may reach the host,
> set `rosbridge_address` to a specific LAN interface and/or front it with a
> curated gateway. rosbridge only bridges topics it can see over DDS — to surface
> the Humble nodes' topics they must be interoperating on domain 42.

---

## Joystick publisher (plug-and-play)

The `joy` role installs a **plug-and-play** joystick publisher on a host. The
host does nothing until a controller is plugged in; a udev rule then auto-starts
a per-device systemd service (`ros-joy@jsN`) that runs a `joy_linux` node
publishing `sensor_msgs/Joy` on `/<hostname>/joy/<N>`. Unplug the controller and
the service stops (the unit `BindsTo` the device). It's enabled for every
Raspberry Pi via `group_vars/raspberry_pis.yml`:

```yaml
install_joy: true          # idle until a joystick is plugged in
# joy_autorepeat_rate: 20.0   # republish rate (Hz) so viewers show steady Hz
# joy_deadzone: 0.05
```

Deploy: `ansible-playbook playbooks/joy.yml --limit raspberry_pis` (also part of
`site.yml`). Then plug a controller into any Pi and its topic appears:

```bash
# from the test/ clients (no ROS install):
uv run list_topics.py --filter joy        # see /vidar/joy/0, etc.
uv run joydump.py                          # live axes/buttons viewer
```

- **`<N>` is a stable per-controller index.** The launcher keys each controller
  by its `/dev/input/by-id` identity (vendor+model+serial) and persists the
  mapping in `/var/lib/ros-joy/index.map`, so the same stick keeps the same `N`
  across replug/reboot (fills the lowest free slot for a new one). Controllers
  with no unique serial fall back to their USB port path.
- **Hostnames are sanitized** to valid ROS names (`[A-Za-z0-9_]`, no leading
  digit) — e.g. `docker-host1` → `/docker_host1/joy/0`.
- The publisher runs as the `ansible` account (added to the `input` group for
  `/dev/input/jsN` access); override with `joy_user`.

---

## Docker setup

The `docker_ros.yml` playbook:

1. Installs Docker CE from the official repository (amd64 or arm64).
2. Installs QEMU binfmt support for multi-arch images (useful on Intel hosts
   pulling arm64 images, or vice-versa).
3. Deploys a `docker-compose.yml` in `/opt/ros-docker/` that runs ROS with
   `network_mode: host` — the container shares the host's network interface
   and is indistinguishable from a bare-metal node on the ROS network.
4. Registers the container as a systemd service so it starts on boot.

### macOS note

Docker Desktop **and** OrbStack on macOS are NAT-only — neither a container
nor an OrbStack Linux machine can join the LAN's DDS multicast domain, so they
**cannot** discover the physical robots.  Run a Mac ROS node in a UTM VM with
**bridged** networking instead (see below).

---

## ROS on a Mac (UTM)

To run a full ROS 2 node *on the Mac* that actually joins the robot fleet
(domain 42, talking to vidar/agony/torture), use a **UTM** virtual machine with
**bridged** networking.  Bridged gives the VM a real `192.168.1.x` DHCP lease,
so it is a true LAN peer and DDS multicast discovery works — exactly like a
physical node.  Containers / OrbStack (NAT) can't do this.

Once the VM exists, it's **just another `ros_nodes` host** — the same
`bootstrap` → `site.yml` flow used for every machine.  No special playbook.

**1. Create the VM in UTM (one-time, GUI, ~10 min)**

- New VM → **Virtualize** (Apple Virtualization, fast on Apple Silicon) → Linux.
- Boot ISO: **Ubuntu Server 24.04 LTS arm64** (24.04 → ROS 2 Kilted).
- Resources: **4 CPU / 8 GiB RAM / 60 GiB disk**.
- **Network → Network Mode: Bridged** (bridged to `en0`).  ← the whole point.
- In the Ubuntu installer: hostname `macvm`, create user **`jtl`** (matches the
  robots' login convention), and enable **"Install OpenSSH server."**

> Prefer rviz in a local window over X11 forwarding? Install the Ubuntu
> **Desktop** ISO instead and run rviz2 directly in the UTM display — heavier
> VM, no XQuartz needed.  Otherwise keep Server + the X11→XQuartz path below.

**2. Onboard it like any node** (`inventory/host_vars/macvm.yml` already sets
`ros_version: kilted`, `ros_package_variant: desktop`, `configure_xwindows: true`):

```bash
# Add macvm's bridged IP under [ros_nodes] in inventory/hosts.ini:
#   macvm  ansible_host=192.168.1.<x>

./scripts/bootstrap-ansible-user.sh -u jtl -K macvm   # create the ansible account
ansible-playbook playbooks/site.yml --limit macvm     # common + ROS Kilted desktop + xwindows
```

**3. GUI shell from the Mac** (requires XQuartz, already on most setups):

```bash
./scripts/ros-mac-shell.sh            # GUI-ready shell (X11 → XQuartz)
./scripts/ros-mac-shell.sh -- rviz2   # launch rviz2 directly
./scripts/ros-mac-shell.sh -n         # headless shell, no XQuartz
```

**Verify fleet discovery:** run `ros2 run demo_nodes_cpp talker` on a robot,
then on `macvm` `ROS_DOMAIN_ID=42 ros2 topic list` should show `/chatter`.

> If `macvm` can't see the robots, double-check the VM's IP is `192.168.1.x`
> (bridged) and **not** a NAT range — that's the #1 cause.  DHCP can also
> reassign the IP across reboots; update the `ansible_host=` line if it changes.

---

## Multi-architecture Docker images

Pre-built Dockerfiles live in `docker/humble/` and `docker/kilted/`.
Build and push multi-arch images with:

```bash
docker buildx create --use
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t your-registry/ros-kilted:latest \
  --push docker/kilted/
```

---

## ROS 2 network architecture

```
                     ┌──────────────────────────────┐
                     │   Physical LAN / VLAN         │
  ┌──────────────────┼──────────────────────────────┐│
  │  agony (amd64)   │  ROS_DOMAIN_ID=42             ││
  │  192.168.1.11    │  DDS peer discovery           ││
  └──────────────────┼──────────────────────────────┘│
  ┌──────────────────┼──────────────────────────────┐│
  │  raspi1 (arm64)  │  ROS_DOMAIN_ID=42             ││
  │  192.168.1.20    │  DDS peer discovery           ││
  └──────────────────┼──────────────────────────────┘│
  ┌──────────────────┼──────────────────────────────┐│
  │  docker-host1    │  Container uses --network host ││
  │  192.168.1.30    │  ROS_DOMAIN_ID=42             ││
  │  └─ ros container│  (same IP as host)            ││
  └──────────────────┴──────────────────────────────┘│
                     └──────────────────────────────┘
```

All nodes — bare-metal, VM, or Docker container — share `ROS_DOMAIN_ID`
and discover each other automatically via DDS. No central master needed.

---

## Variables reference

| Variable | Default | Description |
|---|---|---|
| `ros_version` | `kilted` | `humble` / `kilted` |
| `ros_package_variant` | `ros-base` | `ros-base` or `desktop` |
| `ros_domain_id` | `42` | ROS 2 DDS domain (0–232) |
| `ros_node_ip` | `ansible_host` | IP this node advertises to ROS |
| `configure_xwindows` | `false` | Enable X11 forwarding role |
| `install_docker` | `false` | Enable Docker role |
| `ros_in_docker` | `false` | Enable ros_docker role |
| `xwindows_install_xvfb` | `false` | Install Xvfb virtual framebuffer |
| `desktop_install_firefox` | `true` | In `desktop` role: install Firefox from Mozilla apt repo (not snap) as the default browser |
| `ansible_managed_user` | `ansible` | Service account name (roles/ansible_user) |
| `ansible_managed_user_sudo` | `true` | Grant NOPASSWD sudo to the service account |

Override any variable per-host in `inventory/host_vars/<hostname>.yml` or
per-group in `inventory/group_vars/<group>.yml`.

---

## Adding a new developer

1. They generate an age key: `age-keygen -o ~/.config/sops/age/keys.txt`
2. Add their age public key to `config/sops.yaml` and re-encrypt the ansible key:
   ```bash
   SOPS_CONFIG=config/sops.yaml sops updatekeys config/keys/ansible
   ```
3. They run `dotconfig key load ansible` to decrypt the ansible private key.
