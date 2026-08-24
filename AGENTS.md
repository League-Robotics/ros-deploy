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
  gazebo.yml                 Gazebo + ros_gz on simulation hosts
  diffdrive_teleop.yml       Joystick teleop for differential-drive robots
  joint_teleop.yml           Joystick control of articulated joints
  xwindows.yml               X11 forwarding
roles/
  ansible_user/              Creates ansible OS user + sudoers + authorized_key
  common/                    Baseline apt packages, locale, /etc/hosts
  ros/                       ROS 2 installation dispatcher
    tasks/install_humble.yml   Ubuntu 22.04, ROS 2 Humble
    tasks/install_kilted.yml   Ubuntu 24.04, ROS 2 Kilted Kaiju
    tasks/install_lyrical.yml  Ubuntu 26.04, ROS 2 Lyrical Luth
  cameras/                   camera_ros (libcamera) + image transport; one
                             camera_node per camera as a systemd service.
                             On Pi 5/Ubuntu, builds the Raspberry Pi libcamera
                             fork (cameras_build_rpi_libcamera) so the CFE works.
  gazebo/                    Gazebo simulator + ros_gz (the ROS<->Gazebo topic
                             bridge) on simulation hosts. Gazebo release is
                             derived from ros_version via REP-2000 (kilted ->
                             Ionic, lyrical -> Jetty) and installed as ROS
                             *vendor* packages from packages.ros.org — do NOT
                             add the OSRF repo on top. Also installs VirtualGL
                             so the VNC session renders on the real GPU instead
                             of llvmpipe (~10x on heavy scenes); see
                             docs/wiki/gazebo.md.
  diffdrive_teleop/          Joystick teleop for DIFFERENTIAL-drive robots:
                             gamepad -> drive (linear.x) + turn (angular.z).
                             Never emits linear.y — a diff drive cannot strafe.
                             Deliberately NOT a mode inside roles/xdrive, which
                             is the holonomic X-drive. Ships joy_probe to read a
                             pad's real axis indices/signs instead of guessing.
  joint_teleop/              Joystick control of articulated joints by POSITION
                             (flippers, arm, gripper). Rate-based: hold to move,
                             release and it stays. Separate from the drive teleop
                             packages — chassis velocity and joint position fail
                             differently and want different defaults.
  swerve_drive/              Twist -> swerve-module mixing for SIMULATED swerve
                             robots (ros_pkgs/swerve_drive): one body-frame
                             Twist in, a steer position + wheel velocity per
                             corner out, nearest-branch flip optimisation.
                             Module geometry is host_vars data (see the
                             patribots model's UPSTREAM.md in urdf-collection).
                             Twist source: revhub's motion_control (holonomic).
  gz_bridge/                 Persistent ros_gz parameter_bridge as a systemd
                             unit, topics listed in host_vars. Runs as the
                             shared ros user ON PURPOSE: gz-transport's default
                             partition is <host>:<user>, so a bridge under any
                             other account silently cannot see the desktop
                             session's simulator.
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
  flash-pi-card.sh           Flash Ubuntu 24.04 to an SD card + cloud-init
                             pre-seed (hostname, Busboom Mesh static IP, SSH,
                             ansible user) so a new Pi boots Ansible-ready.
                             macOS only; see the flash-pi-card skill.
  reimage-pi.sh              Interactive picker over flash-pi-card.sh: lists the
                             [raspberry_pis] with IP/model/live status, fills in
                             -n/-i/--model from inventory, preflights the env,
                             warns before wiping a running node, then offers to
                             run site.yml. Never selects the target disk itself.
  camera-portal.py           Discover every fleet camera and view them all in a
                             browser (probes web_video_server; --list for CLI)
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
| `ros_version`         | `humble`/`kilted`/`lyrical` | Which ROS 2 release (set by the host's Ubuntu) |
| `ros_package_variant` | `ros-base` / `desktop` | Minimal or full install |
| `ros_domain_id`       | `42`                   | ROS 2 DDS domain (shared by all nodes) |
| `configure_xwindows`  | `false`                | Run xwindows role |
| `install_gazebo`      | `false`                | Run gazebo role (simulation host) |
| `diffdrive_teleop_enabled` | `false`           | Joystick teleop for a differential drive |
| `joint_teleop_enabled` | `false`              | Joystick control of articulated joints |
| `swerve_drive_enabled` | `false`              | Twist -> swerve mixing for a simulated swerve robot |
| `gz_bridge_enabled`    | `false`              | Persistent ROS <-> Gazebo topic bridge (sim hosts) |
| `desktop_vnc_virtualgl` | `false`              | Run the VNC session under VirtualGL (GPU, not llvmpipe) |
| `install_docker`      | `false`                | Run docker role |
| `ros_in_docker`       | `false`                | Run ros_docker role |
| `ansible_managed_user`| `ansible`              | Service account name |
| `ansible_managed_user_sudo` | `true`         | NOPASSWD sudo granted |

---

## ROS 2 version matrix

| `ros_version` | ROS release            | Ubuntu | Gazebo   | `ros_package_variant` options |
|---------------|------------------------|--------|----------|-------------------------------|
| `humble`      | ROS 2 Humble Hawksbill | 22.04  | Fortress | `ros-base`, `desktop`         |
| `kilted`      | ROS 2 Kilted Kaiju     | 24.04  | Ionic    | `ros-base`, `desktop`         |
| `lyrical`     | ROS 2 Lyrical Luth     | 26.04  | Jetty    | `ros-base`, `desktop`         |

Kilted and Lyrical repo setup uses the `ros2-apt-source` deb package (not the
legacy apt-key method).  See `roles/ros/tasks/install_kilted.yml` and
`install_lyrical.yml`.

**The Ubuntu release picks the ROS release — it is not a preference.**
packages.ros.org builds exactly one ROS distro per Ubuntu suite: `noble`
carries only `ros-kilted-*`, `resolute` only `ros-lyrical-*`.  So a 26.04 host
*cannot* join the fleet on Kilted, and the fleet is currently split: the Pis and
robots run Kilted on 24.04, `buzzkill` runs Lyrical on 26.04.  ROS 2 does not
guarantee cross-distro communication — stock message types usually interoperate,
but a changed definition fails to match silently instead of erroring.  Kilted is
EOL Nov 2026, so the resolution is to move the fleet forward.

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

#### Ubuntu 26.04 hosts: bootstrap needs `ansible_become_exe`

26.04 ships **sudo-rs** as the default `sudo`, and it wraps the password prompt:

```
[sudo: <ansible's prompt>] Password:        # sudo-rs
<ansible's prompt>                          # classic sudo
```

Ansible matches the prompt at the *start* of a line, so it never fires and the
bootstrap dies with `Timeout (12s) waiting for privilege escalation prompt`.
Classic sudo is still installed alongside it — point become at it for the
bootstrap run only:

```bash
ansible-playbook playbooks/bootstrap.yml --limit <host> \
  -u <your_user> --ask-become-pass \
  -e target_hosts=<host> -e bootstrap_user=<your_user> \
  -e ansible_become_exe=/usr/bin/sudo.ws
```

**This is a bootstrap-only workaround.** Every run after it connects as the
`ansible` account, which has NOPASSWD sudo, so Ansible passes `-n`, no prompt is
ever printed, and sudo-rs works fine. Do not put `ansible_become_exe` in
`host_vars` — it would paper over a broken sudoers file later on.

---

## Fleet connection portal

`fleet-portal/serve.py` is a stdlib-only LAN web server that gives agents on the
garage network their connection info for the fleet, served at
`http://192.168.1.40:8770/`.  The portal page itself documents how to connect and
how to keep the portal running — read it there, not here (the details are kept off
the public web on purpose).  It's a plain background process, **not** a systemd
service; if it's not answering, restart it (kill any stale instance first, since a
long-running one serves stale on-disk state):

```bash
lsof -iTCP:8770 -sTCP:LISTEN -n -P         # is it up?
dotconfig key load ansible
pkill -f 'fleet-portal/serve.py' || true
nohup ./fleet-portal/serve.py >/tmp/fleet-portal.log 2>&1 &
```

---

## Common tasks for agents

### Add a new host

1. Add an entry under the appropriate group in `inventory/hosts.ini`.
2. Optionally create `inventory/host_vars/<hostname>.yml` for overrides.
3. Optionally set a static IP: `./scripts/set-static-ip.sh -u <user> -i <ip> <hostname>`
4. Bootstrap: `./scripts/bootstrap-ansible-user.sh -u <user> -K <hostname>`
5. Deploy: `ansible-playbook playbooks/site.yml --limit <hostname>`

For a **brand-new Pi from a blank SD card**, `./scripts/flash-pi-card.sh`
does the equivalent of steps 3–4 (static IP + ansible user + SSH) at flash
time via cloud-init, so you only need steps 1–2 and 5. See the `flash-pi-card`
skill for the guided flow.

To **reimage a Pi already in the inventory**, use `./scripts/reimage-pi.sh` —
it picks the host from `[raspberry_pis]`, supplies `-n`/`-i`/`--model` from
`host_vars` (`pi_model:`), and offers to run step 5 once the card boots. It
still requires a human to confirm the target disk, and makes you type the
hostname before wiping a node that is currently up.

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
