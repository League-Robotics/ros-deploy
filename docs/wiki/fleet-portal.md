---
title: The Fleet Connection Portal
blurb: A LAN web server that hands any agent an SSH key + host aliases so it can reach the fleet with no username or password — plus how to keep it running.
order: 15
tags: [ros, ssh, onboarding, agents, portal]
---

# The Fleet Connection Portal

The **fleet portal** is a tiny LAN web server ([`fleet-portal/serve.py`](https://github.com/League-Robotics/ros-deploy/blob/HEAD/fleet-portal/serve.py))
that lets any other machine or agent on the network connect to the ROS fleet
**without knowing a username or password.** It hands out a pre-authorized SSH
key plus ready-made host aliases, and points at the credential-free rosbridge
gateways for ROS. If you are an agent that needs to reach the robots, start here.

This page is published on the League hub at
<http://robots.jointheleague.org/subsystems/ros-deploy/> — that is the canonical
way any agent discovers the portal. If the portal itself isn't answering when
you arrive, the [Keeping it running](#keeping-it-running-operators--agents)
section below has everything you need to bring it back up.

## Where it is (our environment)

| Thing | Value | Notes |
|-------|-------|-------|
| **Host** | the Mac at `192.168.1.40` | Any machine with the repo + decrypted key can run it. |
| **URL** | `http://192.168.1.40:8770/` | The human/agent-readable portal page. |
| **Port** | `8770` | Bind is `0.0.0.0` → reachable from anywhere on the LAN. |
| **Login user** | `ansible` | Baked into the SSH aliases — you never type it. Has NOPASSWD sudo. |
| **ROS** | `ws://<host>:9090` | rosbridge gateways (`agony`, `torture`, `ali`), no auth. |
| **DDS domain** | `42` | The fleet domain. |

## Connect in one command

Run this on whatever machine you want to connect *from*. It fetches the fleet
key, installs SSH aliases, and leaves you able to `ssh agony` (and the rest)
with no further input:

```bash
curl -fsSL http://192.168.1.40:8770/connect.sh | sh
ssh agony hostname
```

You are now logged in as the shared `ansible` account — no username, no password.

## What it serves

| Route | What you get |
|-------|--------------|
| `GET /` | The portal page (this content, rendered). |
| `GET /connect.sh` | One-shot installer: key + SSH aliases wired up. |
| `GET /ssh_config` | Drop-in `~/.ssh/config` block (aliases with user + key). |
| `GET /keys/fleet_id` | The SSH **private key** that authorizes the fleet. The one secret. |
| `GET /fleet.json` | Machine-readable inventory: hosts, IPs, domain ID, wiki URLs. |
| `GET /wiki` | Index of these wiki pages, served raw over the LAN. |
| `GET /wiki/<page>.md` | A wiki page (like this one) as raw Markdown — curl it, no browser. |

## Running `ros2` over SSH

A non-interactive SSH session does **not** set `ROS_DOMAIN_ID` or source ROS, so
`ros2` silently talks to domain 0 and sees an empty fleet. Always prefix it:

```bash
ssh agony 'ROS_DOMAIN_ID=42 bash -lc "source /opt/ros/*/setup.bash && ros2 topic list"'
```

## Keeping it running (operators / agents)

The portal is a plain background process — it is **not** a managed systemd
service, so nothing restarts it automatically. If it dies (machine reboot,
someone kills the terminal, an `OOM`), the fleet loses its credential-free
onboarding path until someone starts it again.

**Check whether it's up:**

```bash
lsof -iTCP:8770 -sTCP:LISTEN -n -P        # any listener?
curl -fsS http://192.168.1.40:8770/fleet.json >/dev/null && echo up || echo DOWN
```

**Start (or restart) it.** Run from a repo checkout on the host, with the key
decrypted. Restart cleanly by killing any existing instance first — a stale
process serves whatever `serve.py` / wiki pages looked like when it *started*,
so after editing `serve.py`, the host list, or a wiki page you **must** restart
it to pick up the change:

```bash
dotconfig key load ansible                # ensure config/files/ansible exists
pkill -f 'fleet-portal/serve.py' || true  # drop any stale/old instance
nohup ./fleet-portal/serve.py >/tmp/fleet-portal.log 2>&1 &
```

It is healthy practice to **kill and restart it periodically** (e.g. after any
change to the repo's hosts, keys, or wiki, and whenever it has been running for
a long time) so it never drifts from what's on disk. There is no harm in a
restart beyond a ~1s gap in availability.

## Security

This is a **lab-network broker by design**: anything that can reach
`192.168.1.40:8770` can download the fleet's SSH private key from
`/keys/fleet_id`. Keep it on the trusted LAN only — never expose the port
beyond it. If the key is ever exposed, rotate it:

```bash
dotconfig key rm ansible && dotconfig key gen ansible && dotconfig key load ansible
./scripts/bootstrap-ansible-user.sh -u <human_user> -K   # re-authorize every host
```

## Reference

- In-repo: [`fleet-portal/`](https://github.com/League-Robotics/ros-deploy/blob/HEAD/fleet-portal/README.md)
  (server, page, README).
- Related: [Using the rosbridge Gateway](rosbridge.md) — the ROS-over-WebSocket
  path the portal points agents at.
