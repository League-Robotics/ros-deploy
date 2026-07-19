# fleet-portal

A tiny LAN web server that lets **other agents on the network connect to the ROS
fleet without knowing any username or password.**

## What it is

`serve.py` is a single-file, stdlib-only HTTP server (no dependencies). Run it on
any machine that has this repo checked out and the decrypted Ansible key present.
It serves:

| Route | What it gives an agent |
|-------|------------------------|
| `GET /` | The connection portal page (`index.html`) — human/agent-readable instructions |
| `GET /connect.sh` | One-shot installer: `curl … \| sh` and SSH aliases just work |
| `GET /ssh_config` | Drop-in `~/.ssh/config` block (host aliases with user + key pre-filled) |
| `GET /keys/fleet_id` | The SSH **private key** that authorizes the fleet — the one secret |
| `GET /keys/fleet_id.pub` | The matching public key |
| `GET /fleet.json` | Machine-readable inventory (hosts, IPs, domain ID, artifact URLs) |

## How "no username / password" works

- **SSH** — the agent authenticates with the key this server hands out, logging in
  as the `ansible` service account (which has NOPASSWD sudo on every node). The
  username and key path live inside the SSH config alias, so the agent just runs
  `ssh agony` — no password is ever typed. The `ansible` account is the one the
  served key (`config/files/ansible` → `config/keys/ansible.pub`, deployed by
  `roles/ansible_user`) actually authorizes fleet-wide.
- **ROS** — the gateway hosts run **rosbridge** at `ws://<host>:9090`, which needs
  **no ROS credentials at all**. An agent pubs/subs topics by opening a WebSocket.

## Run it

```bash
dotconfig key load ansible          # decrypt config/files/ansible for this session
./fleet-portal/serve.py             # binds 0.0.0.0:8770 → http://<this-machine>:8770/
./fleet-portal/serve.py --port 9000 # different port
./fleet-portal/serve.py --host 192.168.1.11   # bind one interface only
```

Then, from any other machine on the LAN:

```bash
curl -fsSL http://<portal-host>:8770/connect.sh | sh
ssh agony hostname
```

## Hosts advertised

`agony` (192.168.1.11), `torture` (192.168.1.12), `ali` (192.168.1.143),
`nepr` (192.168.1.146), `vidar` (192.168.1.144). Addresses come from
`inventory/hosts.ini`; edit the `FLEET` list in `serve.py` to add/remove hosts.
All nodes share `ROS_DOMAIN_ID=42`.

## Running `ros2` over SSH

A non-interactive SSH session doesn't set `ROS_DOMAIN_ID` or source ROS, so
`ros2` defaults to domain 0 and sees nothing. Always prefix it:

```bash
ssh agony 'ROS_DOMAIN_ID=42 bash -lc "source /opt/ros/*/setup.bash && ros2 topic list"'
```

## Security

This is a **lab-network broker by design**: anything that can reach the port can
download the fleet private key from `/keys/fleet_id`. Keep it on the trusted LAN.
If the key leaks beyond it, rotate:

```bash
dotconfig key rm ansible && dotconfig key gen ansible && dotconfig key load ansible
./scripts/bootstrap-ansible-user.sh -u <human_user> -K   # re-authorize every host
```
