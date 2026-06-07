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
