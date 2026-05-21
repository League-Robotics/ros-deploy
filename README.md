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
| Mac / emulation | Multi-arch Docker images via `buildx`; QEMU binfmt support on Linux hosts |
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
│   └── docker_ros.yml           # Docker + ROS container only
├── roles/
│   ├── ansible_user/            # Creates ansible OS user + deploys SSH key
│   ├── common/                  # Baseline packages, /etc/hosts entries
│   ├── ros/                     # ROS 2 install (Humble / Kilted)
│   ├── xwindows/                # SSH X11 forwarding, optional Xvfb
│   ├── docker/                  # Docker CE install (multi-arch)
│   └── ros_docker/              # docker-compose + systemd service for ROS
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
```

### 6 — Verify ROS 2 networking

```bash
ssh ubuntu@192.168.1.11
ros2 topic list        # should list /parameter_events etc.
ros2 node list
```

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
