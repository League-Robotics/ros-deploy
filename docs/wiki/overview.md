---
title: ROS Deploy Overview
blurb: What the ROS 2 deployment system is and how to use it.
order: 10
tags: [ros, ansible, overview]
---

# ROS Deploy Overview

**ros-deploy** is the Ansible project that installs and configures ROS 2 (Robot
Operating System) across the whole League Robotics fleet — physical robots,
Raspberry Pis, Ubuntu VMs, and Docker containers. One set of playbooks brings every
node to the same known-good state, so adding or rebuilding a robot is repeatable
instead of hand-crafted.

## What it does

- Installs ROS 2 (**Humble** on Ubuntu 22.04, **Kilted** on Ubuntu 24.04) at the
  `ros-base` or `desktop` variant.
- Puts every node on a shared DDS domain (`ros_domain_id: 42`) so nodes discover
  each other automatically over the LAN.
- Provisions optional capabilities per host: cameras, joystick publishing, a
  rosbridge WebSocket gateway, X11 forwarding, Docker, and a heartbeat check.
- Manages the `ansible` service account and all SSH keys / secrets through
  **dotconfig** (SOPS-encrypted), so credentials never live in the repo in plaintext.

## How it's organized

| Path | Purpose |
|------|---------|
| `inventory/hosts.ini` | The fleet — host IPs and group membership. Edit this to describe the network. |
| `playbooks/` | One playbook per job (`site.yml` runs everything; `ros_install.yml`, `cameras.yml`, `joy.yml`, etc. run one piece). |
| `roles/` | The reusable units the playbooks apply (`ros`, `cameras`, `joy`, `rosbridge`, `common`, …). |
| `inventory/group_vars` / `host_vars` | Configuration: defaults for all hosts, plus per-host overrides. |
| `config/` | dotconfig tree — encrypted `ansible` SSH key and secrets. |

## Using it: the short version

```bash
# 1. Load the service SSH key for this session (decrypts into config/files/)
dotconfig key load ansible

# 2. Deploy everything to every host in the inventory
ansible-playbook playbooks/site.yml

# ...or just one host
ansible-playbook playbooks/site.yml --limit <hostname>

# ...or just one capability
ansible-playbook playbooks/cameras.yml --limit <hostname>
```

Re-running is always safe — every task is idempotent, so applying a playbook to an
already-configured host changes nothing.

## Where to go next

- **[Setting Up a New Node](new-node.md)** — bootstrap and deploy a brand-new robot.
- **[ROS Packages & Capabilities](packages.md)** — what the fleet actually provides.
- **[Fleet Administration](administration.md)** — distributing packages and config
  across the network.

Full reference docs live in the repo's
[README](https://github.com/League-Robotics/ros-deploy/blob/HEAD/README.md) and the
agent guide
[AGENTS.md](https://github.com/League-Robotics/ros-deploy/blob/HEAD/AGENTS.md).
