---
title: ROS Packages & Capabilities
blurb: The ROS 2 nodes and capabilities the fleet provides — heartbeat, cameras, joystick, rosbridge.
order: 30
tags: [ros, packages, capabilities]
---

# ROS Packages & Capabilities

Beyond a base ROS 2 install, ros-deploy ships a set of capabilities you can turn on
per host. Each is delivered by an Ansible role (and matching `playbooks/<name>.yml`)
that installs the package and registers it as a **systemd service**, so it starts on
boot and restarts on failure.

## In-repo ROS packages

- **heartbeat** (`ros_pkgs/heartbeat`, role `heartbeat`) — a small connectivity-check
  node that publishes a periodic liveness signal, used to confirm a node is up and
  visible on the DDS domain. Deploy with `playbooks/heartbeat.yml`.

## Capability roles

| Capability | Role / Playbook | What it provides |
|------------|-----------------|------------------|
| **Cameras** | `cameras` / `cameras.yml` | `camera_ros` (libcamera) with one `camera_node` per camera as a systemd service. On Pi 5 it builds the Raspberry Pi libcamera fork so the camera front-end works. |
| **Joystick** | `joy` / `joy.yml` | Plug-and-play joystick publisher with udev/systemd auto-detection and stable per-controller indexing. |
| **rosbridge** | `rosbridge` / `rosbridge.yml` | JSON-over-WebSocket gateway so non-ROS clients (browser via roslib.js, Python via roslibpy) can talk to the fleet. |
| **X Windows** | `xwindows` / `xwindows.yml` | sshd X11 forwarding + xauth (optional headless Xvfb) for remote rviz/rqt. |
| **Desktop** | `desktop` / `desktop.yml` | Desktop-environment extras for full GUI nodes. |
| **Docker ROS** | `ros_docker` / `docker_ros.yml` | Containerised ROS via docker-compose + systemd, `--network host` so DDS discovery works. |
| **Shared `ros` user** | `ros_user` / `ros_user.yml` | A managed `ros` zsh account with the ROS overlay auto-sourced. |
| **Fleet WiFi** | `wifi` / `wifi.yml` | Joins Raspberry Pis to the Busboom Mesh SSID for multicast DDS discovery. |

## Choosing what a host gets

`site.yml` applies roles based on group membership in `inventory/hosts.ini` and
feature flags in `group_vars` / `host_vars` (e.g. `install_docker`,
`configure_xwindows`, `ros_in_docker`). To add a single capability to an existing
host, run just that playbook:

```bash
ansible-playbook playbooks/rosbridge.yml --limit <hostname>
```

See [Fleet Administration](administration.md) for how these get distributed across
the whole network at once.
