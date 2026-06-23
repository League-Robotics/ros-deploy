# AGENTS.md — ros-deploy

Operational reference for AI agents (GitHub Copilot, Claude, etc.) working
in this repository.

---

## What this project is

An Ansible project that deploys ROS 2 (Robot Operating System) to Ubuntu hosts:
physical robots, Raspberry Pis, VMs, and Docker containers.  All SSH credentials
and encrypted secrets are managed with **dotconfig**.

---

## Repository map

```
ansible.cfg                  Global Ansible config (remote_user=ansible,
                             private_key_file=config/files/ansible)
inventory/
  hosts.ini                  Host IPs and group assignments — edit for the network
  group_vars/all.yml         Variables shared across all hosts
  group_vars/ros_docker_hosts.yml  Docker host extras
  host_vars/<name>.yml       Per-host overrides (ros_version, etc.)
playbooks/
  bootstrap.yml              ONE-TIME: create the ansible OS user on a new host
  site.yml                   Full deployment (all roles, all groups)
  ros_install.yml            ROS 2 only (physical/VM nodes)
  docker_ros.yml             Docker + ROS container
  xwindows.yml               X11 forwarding
roles/
  ansible_user/              Creates ansible OS user + sudoers + authorized_key
  common/                    Baseline apt packages, locale, /etc/hosts
  ros/                       ROS 2 installation dispatcher
    tasks/install_humble.yml   Ubuntu 22.04, ROS 2 Humble
    tasks/install_kilted.yml   Ubuntu 24.04, ROS 2 Kilted Kaiju
  cameras/                   camera_ros (libcamera) + image transport; one
                             camera_node per camera as a systemd service.
                             On Pi 5/Ubuntu, builds the Raspberry Pi libcamera
                             fork (cameras_build_rpi_libcamera) so the CFE works.
  heartbeat/                 Builds the local ros_pkgs/heartbeat package on nodes
  fleet_packages/            Distributes ROS packages collected from other
                             LeagueRobotics repos (see docs/fleet-packages.md)
  ros_docker/                docker-compose + systemd for containerised ROS
  docker/                    Docker CE installation
  xwindows/                  sshd X11Forwarding + xauth + optional Xvfb
docker/
  humble/Dockerfile          Multi-arch ROS 2 Humble image
  kilted/Dockerfile          Multi-arch ROS 2 Kilted image
scripts/
  bootstrap-ansible-user.sh  Shell wrapper: decrypts key → runs bootstrap.yml
  set-static-ip.sh           Configure a static IP on a remote host via SSH
  ros-mac-shell.sh           SSH into the macvm VM with X11 → XQuartz (rviz/rqt)
  collect_fleet_packages.py  Scan the LeagueRobotics org for fleet ROS packages,
                             clone them, write .fleet/packages.lock.yml
docs/
  fleet-packages.md          How other repos ship ROS packages to the fleet
config/                      dotconfig tree (keys, secrets, env files)
  keys/ansible               SOPS-encrypted ansible private key
  keys/ansible.pub           Ansible public key (plaintext)
  files/ansible              Decrypted private key (gitignored, session-local)
```

---

## Key variables (`group_vars/all.yml`)

| Variable              | Values / Default       | Meaning |
|-----------------------|------------------------|---------|
| `ros_version`         | `humble` / `kilted`    | Which ROS 2 release to install |
| `ros_package_variant` | `ros-base` / `desktop` | Minimal or full install |
| `ros_domain_id`       | `42`                   | ROS 2 DDS domain (shared by all nodes) |
| `configure_xwindows`  | `false`                | Run xwindows role |
| `install_docker`      | `false`                | Run docker role |
| `ros_in_docker`       | `false`                | Run ros_docker role |
| `ansible_managed_user`| `ansible`              | Service account name |
| `ansible_managed_user_sudo` | `true`         | NOPASSWD sudo granted |

---

## ROS 2 version matrix

| `ros_version` | ROS release            | Ubuntu | `ros_package_variant` options |
|---------------|------------------------|--------|-------------------------------|
| `humble`      | ROS 2 Humble Hawksbill | 22.04  | `ros-base`, `desktop`         |
| `kilted`      | ROS 2 Kilted Kaiju     | 24.04  | `ros-base`, `desktop`         |

Kilted repo setup uses the `ros2-apt-source` deb package (not the legacy
apt-key method).  See `roles/ros/tasks/install_kilted.yml`.

---

## SSH / authentication model

All Ansible runs connect as the `ansible` OS user.  The private key lives
at `config/files/ansible` (decrypted by dotconfig, gitignored).

```
dotconfig key gen ansible      # generate once; stores encrypted in config/keys/
dotconfig key load ansible     # decrypt to config/files/ansible for this session
```

`ansible.cfg` picks up `remote_user = ansible` and
`private_key_file = config/files/ansible` automatically — no extra flags needed.

### Bootstrap flow (new host)

The `ansible` user does not exist on a freshly provisioned host.  Use the
bootstrap script or playbook to create it using the human operator's credentials:

```bash
# Helper script (recommended):
./scripts/bootstrap-ansible-user.sh -u <human_user> -K [host ...]

# Or directly:
ansible-playbook playbooks/bootstrap.yml -u <human_user> --ask-become-pass
```

The script auto-detects your personal SSH key (`~/.ssh/id_ed25519` or
`~/.ssh/id_rsa`) and overrides the `ansible.cfg` service key so the initial
connection uses your own credentials.

---

## Common tasks for agents

### Add a new host

1. Add an entry under the appropriate group in `inventory/hosts.ini`.
2. Optionally create `inventory/host_vars/<hostname>.yml` for overrides.
3. Optionally set a static IP: `./scripts/set-static-ip.sh -u <user> -i <ip> <hostname>`
4. Bootstrap: `./scripts/bootstrap-ansible-user.sh -u <user> -K <hostname>`
5. Deploy: `ansible-playbook playbooks/site.yml --limit <hostname>`

### Running ROS on a Mac

`macvm` is the ROS node on Eric's Mac: an **Ubuntu 24.04 VM in UTM with
bridged networking**.  Bridged gives it a real LAN IP, so it joins the DDS
domain like any physical node — containers/OrbStack are NAT-only and can't.
The VM is created once via UTM's GUI, then onboarded with the **same** flow as
any host (bootstrap → site.yml).  There is intentionally **no Mac-specific role
or playbook**.  See README "ROS on a Mac (UTM)".  GUI: `scripts/ros-mac-shell.sh`.

### Distribute a ROS package from another LeagueRobotics repo

The package's repo adds the GitHub topic `fleet-ros-package` + a `fleet.yaml`
manifest at its root; `ros-deploy` discovers, clones, and builds it onto the
nodes the manifest targets. No change needed in this repo. Full contract +
workflow: **`docs/fleet-packages.md`**. Control machine needs `vcstool`
(`gh` CLI recommended). Deploy: `ansible-playbook playbooks/fleet_packages.yml`.

### Change the ROS version on a host

Edit (or create) `inventory/host_vars/<hostname>.yml`:

```yaml
ros_version: humble
ros_package_variant: ros-base
```

Re-run: `ansible-playbook playbooks/ros_install.yml --limit <hostname>`

### Add a new ROS distro

1. Create `roles/ros/tasks/install_<distro>.yml` (model on `install_kilted.yml`).
2. Add the distro to the assert list in `roles/ros/tasks/main.yml`.
3. Add an `include_tasks` stanza for the new distro.
4. Add a Docker image entry in `inventory/group_vars/all.yml`
   (`ros_<distro>_image`).
5. Update the `_ros_docker_image` fact in `roles/ros_docker/tasks/main.yml`.
6. Create `docker/<distro>/Dockerfile` and `entrypoint.sh`.

### Rebuild the Ansible SSH key

```bash
dotconfig key rm ansible        # remove old key
dotconfig key gen ansible       # generate new key
dotconfig key load ansible      # decrypt for this session
# Re-bootstrap every host:
./scripts/bootstrap-ansible-user.sh -u <human_user> -K
```

---

## Conventions

- **Become**: all roles require `become: true`; the `ansible` user has
  NOPASSWD sudo, so `--ask-become-pass` is not needed after bootstrap.
- **Idempotency**: every task is idempotent.  Re-running a playbook on an
  already-configured host is safe.
- **Variable precedence**: host_vars > group_vars > role defaults.  Override
  at the most specific scope needed.
- **SOPS secrets**: `secrets.env` files in `config/` are SOPS-encrypted.
  SOPS must be installed and `SOPS_AGE_KEY_FILE` must point to a valid age
  private key file for secret operations to work.
- **Kilted repo setup**: uses the `ros2-apt-source` GitHub release deb —
  the task fetches the latest release tag from the GitHub API at run time.

---

## Collections required

```bash
ansible-galaxy collection install \
  community.general \
  community.docker \
  ansible.posix
```

`ansible.posix.authorized_key` is used by the `ansible_user` role.
`community.docker.docker_image` is used by the `ros_docker` role.
