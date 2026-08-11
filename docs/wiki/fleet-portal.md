---
title: Connecting to the Robot Garage Fleet
blurb: Hosts, SSH access, ROS settings, cameras and rosbridge — everything needed to connect, no portal required.
order: 15
updated: 2026-08-09
tags: [onboarding, connecting, ssh]
---

# Connecting to the Robot Garage Fleet

Everything on this page assumes you are on the garage LAN (WiFi: **Busboom
Mesh** — required for ROS discovery, not just connectivity).

## The fleet

| Host | IP | What it is |
|------|----|-----------|
| `baldur` | 192.168.1.152 (wired: 192.168.2.152) | X-drive robot — REV hub, on-board teleop, 8BitDo pad |
| `nepr` | 192.168.1.146 | Second X-drive robot (parked, uncalibrated) |
| `skadi` | 192.168.1.142 (wired: 192.168.2.142) | Camera server — IMX296 global-shutter (mono) |
| `vidar` | 192.168.1.144 | Camera server — Pi 5, dual CSI |
| `ali` | 192.168.1.143 | Utility Pi — rosbridge, cameras |
| `agony` | 192.168.1.11 | Server — rosbridge gateway |
| `torture` | 192.168.1.12 | Server — rosbridge gateway |
| `gauti` | 192.168.1.145 | Utility Pi (serial-port workbench) |
| `vali` | 192.168.1.151 | Utility Pi |
| `docker-host1` | 192.168.1.30 | Containerised ROS |

`baldur` and `skadi` are also wired to each other; between wired hosts, traffic
(including ROS image streams) automatically takes the ethernet path.

## SSH — one shared account, no key setup

Every fleet node has the managed **`ros`** account:

```bash
ssh ros@<host>          # e.g. ssh ros@baldur or ssh ros@192.168.1.152
# password: robotics
```

You get a zsh shell with the ROS environment and the fleet's colcon overlay
already sourced, and passwordless `sudo`. (Lab-network convenience by design —
this is not a secret, and the fleet is not reachable from outside the LAN.)

Prefer key auth? Drop your public key in the repo's
`ros_user_authorized_keys` list (`roles/ros_user/defaults/main.yml`) and re-run
`ansible-playbook playbooks/ros_user.yml`.

## ROS — the two things everyone forgets

```bash
export ROS_DOMAIN_ID=42                    # the fleet domain; without it you see NOTHING
source /opt/ros/kilted/setup.bash          # (pre-sourced if you log in as ros)
ros2 topic list
```

WiFi machines must be on **Busboom Mesh** — DDS multicast discovery does not
cross SSIDs.

## No ROS installed? Use rosbridge

JSON over WebSocket, no auth, from Python (`pip install roslibpy`) or a browser:

- `ws://agony:9090` · `ws://torture:9090` · `ws://ali:9090`

See [Using the rosbridge Gateway](rosbridge.md) for working snippets.

## Watch the cameras (any browser)

```
http://skadi:8080/stream_viewer?topic=/skadi/camera0/camera/image_color
http://vidar:8080/stream_viewer?topic=/vidar/camera0/camera/image_color
http://ali:8080/stream_viewer?topic=/ali/camera0/camera/image_raw
```

The topic differs per host on purpose: sensors whose colors arrive mislabeled
(IMX296) get a swap-corrected `image_color` stream — view that one. Sensors
that arrive correct (IMX219, IMX708) are viewed on `image_raw` directly.

## History

Connection info used to be served by a small portal on `192.168.1.40:8770`
(`fleet-portal/` in the repo). That server is retired — this page replaces it.
