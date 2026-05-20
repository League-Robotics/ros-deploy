# ros-deploy

Ansible playbooks for deploying ROS (Robot Operating System) to remote
machines — physical hardware, VMs, and Docker containers — across
Intel (amd64), ARM64 (Raspberry Pi 4/5), and emulated/Docker-on-Mac
environments.

## Features

| Capability | Details |
|---|---|
| ROS 1 (Noetic) | Ubuntu 20.04, amd64 + arm64 |
| ROS 2 (Humble) | Ubuntu 22.04, amd64 + arm64 |
| Raspberry Pi | 64-bit Ubuntu (arm64); detected automatically |
| Docker | ROS runs in a container with `--network host` so it joins the same ROS network as physical nodes |
| X Windows | SSH X11 forwarding configured so remote GUI apps render on your local screen |
| Mac / emulation | Multi-arch Docker images via `buildx`; QEMU binfmt support on Linux hosts |
| Network discovery | All nodes — bare-metal, VM, or container — share the same `ROS_MASTER_URI` / `ROS_DOMAIN_ID` so `rostopic list` and `ros2 topic list` see every node |

---

## Repository layout

```
ros-deploy/
├── ansible.cfg                  # Ansible project settings
├── inventory/
│   ├── hosts.ini                # EDIT THIS — your machines
│   ├── group_vars/
│   │   ├── all.yml              # Variables for every host
│   │   ├── ros_master.yml       # Variables for the ROS master
│   │   └── ros_docker_hosts.yml # Variables for Docker hosts
│   └── host_vars/
│       ├── raspi1.yml           # Raspberry Pi example
│       └── docker-host1.yml     # Docker host example
├── playbooks/
│   ├── site.yml                 # Run everything
│   ├── ros_install.yml          # ROS on bare-metal / VMs only
│   ├── xwindows.yml             # X11 forwarding only
│   └── docker_ros.yml           # Docker + ROS container only
├── roles/
│   ├── common/                  # Baseline packages, /etc/hosts entries
│   ├── ros/                     # ROS install (Noetic or Humble)
│   ├── xwindows/                # SSH X11 forwarding, optional Xvfb
│   ├── docker/                  # Docker CE install (multi-arch)
│   └── ros_docker/              # docker-compose + systemd service for ROS
└── docker/
    ├── noetic/                  # Multi-arch Dockerfile for ROS Noetic
    └── humble/                  # Multi-arch Dockerfile for ROS 2 Humble
```

---

## Quick start

### 1 — Install dependencies on your control machine

```bash
pip install ansible
ansible-galaxy collection install community.general community.docker
```

### 2 — Edit the inventory

Copy and customise the example inventory to match your machines:

```bash
cp inventory/hosts.ini inventory/hosts.ini   # already there
$EDITOR inventory/hosts.ini
```

Put the IP addresses and usernames of your machines into the appropriate groups:

```ini
[ros_master]
ros-master ansible_host=192.168.1.10 ansible_user=ubuntu

[ros_workers]
robot1    ansible_host=192.168.1.11 ansible_user=ubuntu
raspi1    ansible_host=192.168.1.20 ansible_user=pi

[ros_docker_hosts]
docker-host1 ansible_host=192.168.1.30 ansible_user=ubuntu
```

Set global variables in `inventory/group_vars/all.yml` — especially `ros_version`
and the `ros_master_hostname` / `ros_master_ip` values.

### 3 — Run a playbook

```bash
# Install ROS on all physical/VM nodes:
ansible-playbook playbooks/ros_install.yml

# Install Docker and deploy ROS containers on docker hosts:
ansible-playbook playbooks/docker_ros.yml

# Set up X11 forwarding on all nodes:
ansible-playbook playbooks/xwindows.yml

# Run everything at once:
ansible-playbook playbooks/site.yml
```

### 4 — Verify ROS networking

After deployment, SSH into any node and check that all nodes see each other:

**ROS 1 (Noetic)**
```bash
ssh -X ubuntu@192.168.1.11
source /opt/ros/noetic/setup.bash
rostopic list          # should list /rosout etc.
rosnode list           # should show every registered node
```

**ROS 2 (Humble)**
```bash
ssh ubuntu@192.168.1.11
source /opt/ros/humble/setup.bash
ros2 topic list        # should list /parameter_events etc.
ros2 node list
```

---

## X Windows forwarding

To display remote GUI applications on your local screen:

```bash
# Trusted X forwarding (use -Y for tools like rviz that need it)
ssh -Y ubuntu@192.168.1.11
# Then launch any GUI:
rviz &
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

Docker Desktop on macOS does **not** support `--network host`.  The best
workaround is to run your Mac as a *control node* only (i.e., run Ansible
from it but not deploy a ROS container on it), or to use a Linux VM.

---

## Multi-architecture Docker images

Pre-built Dockerfiles live in `docker/noetic/` and `docker/humble/`.
Build and push multi-arch images with:

```bash
docker buildx create --use
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t your-registry/ros-noetic:latest \
  --push docker/noetic/
```

---

## ROS network architecture

```
                     ┌──────────────────────────────┐
                     │   Physical LAN / VLAN         │
  ┌──────────────────┼──────────────────────────────┐│
  │  ros-master      │  (runs roscore for ROS 1)     ││
  │  192.168.1.10    │                               ││
  └──────────────────┼──────────────────────────────┘│
  ┌──────────────────┼──────────────────────────────┐│
  │  robot1 (amd64)  │  ROS_MASTER_URI →master:11311 ││
  │  192.168.1.11    │  ROS_IP=192.168.1.11          ││
  └──────────────────┼──────────────────────────────┘│
  ┌──────────────────┼──────────────────────────────┐│
  │  raspi1 (arm64)  │  ROS_MASTER_URI →master:11311 ││
  │  192.168.1.20    │  ROS_IP=192.168.1.20          ││
  └──────────────────┼──────────────────────────────┘│
  ┌──────────────────┼──────────────────────────────┐│
  │  docker-host1    │  Container uses --network host ││
  │  192.168.1.30    │  ROS_IP=192.168.1.30          ││
  │  └─ ros container│  (same IP as host)            ││
  └──────────────────┴──────────────────────────────┘│
                     └──────────────────────────────┘
```

All nodes — regardless of whether they are bare-metal, VM, or Docker
container — advertise their host IP (`ROS_IP` for ROS 1,
`ROS_DOMAIN_ID` for ROS 2) so every other node can reach them directly.

---

## Variables reference

| Variable | Default | Description |
|---|---|---|
| `ros_version` | `noetic` | `noetic` or `humble` |
| `ros_package_variant` | `ros-base` | `ros-base` or `ros-desktop` |
| `ros_master_ip` | first host in `[ros_master]` | IP of the ROS 1 master |
| `ros_master_port` | `11311` | roscore port |
| `ros_master_uri` | computed | Full `http://…:11311` URI |
| `ros_node_ip` | `ansible_host` | IP this node advertises to ROS |
| `ros_domain_id` | `42` | ROS 2 DDS domain (0–232) |
| `configure_xwindows` | `false` | Enable X11 forwarding role |
| `install_docker` | `false` | Enable Docker role |
| `ros_in_docker` | `false` | Enable ros_docker role |
| `xwindows_install_xvfb` | `false` | Install Xvfb virtual framebuffer |

Override any variable per-host in `inventory/host_vars/<hostname>.yml` or
per-group in `inventory/group_vars/<group>.yml`.
