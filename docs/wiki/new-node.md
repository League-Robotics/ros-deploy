---
title: Setting Up a New Node
blurb: Bootstrap and deploy ROS 2 to a new robot, Pi, or VM.
order: 20
tags: [ros, ansible, onboarding]
---

# Setting Up a New Node

Bringing a new machine onto the fleet is a five-step flow. It's the same whether the
node is a physical robot, a Raspberry Pi, or an Ubuntu VM (including the `macvm`
UTM VM on a Mac) — there is no host-specific special-casing.

> **Flashing a Raspberry Pi SD card?** Skip to
> [Reimaging a Raspberry Pi](#reimaging-a-raspberry-pi) below. A flashed card
> arrives with the static IP, SSH and the `ansible` account already in place, so
> steps 3 and 4 of the manual flow do not apply.

## Prerequisites

- The node runs a supported Ubuntu: **22.04** (→ ROS 2 Humble) or **24.04**
  (→ ROS 2 Kilted).
- It's reachable on the LAN and you can SSH in with your own account.
- You have the Ansible collections installed and the service key loaded:
  ```bash
  ansible-galaxy collection install community.general community.docker ansible.posix
  dotconfig key load ansible
  ```

## Steps

**1. Add the host to the inventory.** Put an entry under the right group in
`inventory/hosts.ini`.

**2. (Optional) Per-host overrides.** Create `inventory/host_vars/<hostname>.yml` to
override things like the ROS version:
```yaml
ros_version: kilted
ros_package_variant: ros-base
```

**3. (Optional) Pin a static IP:**
```bash
./scripts/set-static-ip.sh -u <user> -i <ip> <hostname>
```

**4. Bootstrap the service account.** A fresh host has no `ansible` user yet. This
one-time step creates it using *your* credentials:
```bash
./scripts/bootstrap-ansible-user.sh -u <your_user> -K <hostname>
```

**5. Deploy.** From here on every run connects as the `ansible` user automatically:
```bash
ansible-playbook playbooks/site.yml --limit <hostname>
```

## Reimaging a Raspberry Pi

For a Pi, don't install Ubuntu by hand. Flash a card that boots already knowing who
it is — hostname, static IP on Busboom Mesh, SSH on, and the `ansible` account with
its key and NOPASSWD sudo. That covers steps 3–4 above, so a freshly flashed Pi goes
straight to `site.yml`.

```bash
dotconfig load <deploy>; set -a; source .env; set +a   # exports WIFI_MESH_PSK
./scripts/reimage-pi.sh
```

The picker lists every Pi in the `[raspberry_pis]` group with its IP, model and
whether it's currently up, and fills in the flash script's `-n`, `-i` and `--model`
from the inventory. Then:

1. It prints the candidate disks and **stops** — you confirm which `/dev/diskN` is
   the card and re-run with `-d /dev/diskN --confirm /dev/diskN`. It never picks the
   disk for you.
2. If the host you chose is currently **up**, it warns that you're about to replace a
   running node and makes you type the hostname to confirm.
3. After the write it offers to wait for the Pi to boot and run `site.yml` for you.

Useful flags: `--dry-run` (print the flash command, touch nothing), `-n <name>`
(skip the menu), `--no-ping` (faster menu), `--no-deploy`.

Each Pi's model comes from `pi_model:` in its `host_vars` file (`"3" | "4" | "5" |
"zero2w"`). If it's missing, the picker asks and then prints the line to paste in.
There is deliberately no group-level default — a Pi 5 silently treated as a Pi 4 is
exactly the mistake this is meant to prevent.

**ROS is not on the card, by design.** The card carries only the prep layer; ROS
installs via `roles/ros`, because `install_kilted.yml` queries the GitHub API for the
current `ros-apt-source` release at run time and pulls ~193 MB. Duplicating that into
cloud-init would be a second source of truth that can't be re-run and whose failures
land in a log on a Pi you can't reach yet. The picker's deploy step covers it.

See also `scripts/flash-pi-card.sh -h` for the underlying flags (image caching,
`--render-only`, the human admin account, recovery passwords).

## Networking note

All nodes share DDS domain `42` and discover each other via multicast — but
multicast does **not** cross between WiFi SSIDs. Every wireless ROS node must be on
the **Busboom Mesh** SSID, or it won't see the rest of the fleet. The `wifi` role
(`playbooks/wifi.yml`) configures this on Raspberry Pis.

## Running ROS on a Mac

The Mac node (`macvm`) is an Ubuntu 24.04 VM in **UTM with bridged networking** —
bridging gives it a real LAN IP so it joins the DDS domain like any physical host.
Create the VM once via UTM's GUI, then onboard it with the exact same
bootstrap → `site.yml` flow above. For GUI tools (rviz/rqt) over X11:
`./scripts/ros-mac-shell.sh`.
