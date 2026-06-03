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

### Viewing cameras on your Mac (via X11 to XQuartz)

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
