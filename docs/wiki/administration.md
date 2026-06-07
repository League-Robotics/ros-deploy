---
title: Fleet Administration
blurb: Day-to-day admin — distributing packages and configuration across the network.
order: 40
tags: [ros, ansible, admin]
---

# Fleet Administration

Routine fleet work is just re-running playbooks. Because every task is idempotent,
you distribute a change by editing the relevant config or role and re-applying it —
hosts already in the desired state are left untouched.

## Distributing packages and config across the network

The unit of distribution is a **playbook run**. To push a change everywhere:

```bash
# Roll out everything to the whole fleet
ansible-playbook playbooks/site.yml

# Roll out one capability to the whole fleet
ansible-playbook playbooks/cameras.yml

# Target a subset
ansible-playbook playbooks/site.yml --limit ros_docker_hosts
ansible-playbook playbooks/site.yml --limit pi-01,pi-02
```

Typical flows:

- **Change a setting everywhere** — edit `inventory/group_vars/all.yml` (e.g.
  `ros_version`, `ros_package_variant`, `ros_domain_id`), then re-run `site.yml`.
- **Override one host** — edit `inventory/host_vars/<hostname>.yml`, then re-run with
  `--limit <hostname>`. Precedence is **host_vars > group_vars > role defaults**.
- **Update a ROS package** (e.g. `heartbeat`) — change the code under `ros_pkgs/`,
  then re-run that role's playbook to redeploy and restart the systemd service on
  each node.

## Authentication

All runs connect as the `ansible` service account using the SOPS-encrypted key
managed by dotconfig:

```bash
dotconfig key load ansible      # decrypt the key for this session
```

`ansible.cfg` picks up `remote_user = ansible` and the key path automatically — no
extra flags needed. The `ansible` user has NOPASSWD sudo, so `--ask-become-pass` is
not required after a host is bootstrapped.

## Common administrative tasks

- **Add a developer** — generate their age key and re-encrypt secrets with SOPS so
  they can decrypt fleet credentials (see the README).
- **Rotate the Ansible key** — `dotconfig key rm ansible && dotconfig key gen ansible
  && dotconfig key load ansible`, then re-bootstrap every host with
  `./scripts/bootstrap-ansible-user.sh -u <user> -K`.
- **Add a new ROS distro** — add `roles/ros/tasks/install_<distro>.yml` and a Docker
  image entry; see [AGENTS.md](https://github.com/League-Robotics/ros-deploy/blob/HEAD/AGENTS.md)
  for the full checklist.

## Health checks

The `heartbeat` capability publishes liveness on the DDS domain, so a quick way to
confirm a node is reachable and discoverable is to deploy/observe its heartbeat.
Remember that DDS multicast discovery does not cross WiFi SSIDs — every wireless node
must be on the **Busboom Mesh** SSID.
