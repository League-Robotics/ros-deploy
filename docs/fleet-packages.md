# Fleet packages — distributing ROS code from other LeagueRobotics repos

ROS 2 packages that live in **other repos in the LeagueRobotics GitHub org** can be built
onto fleet nodes without copying them into this repo. A repo opts in; `ros-deploy`
discovers it, clones it, and builds the packages it declares onto the nodes it targets.

This is the mechanism for first-party code owned by other teams/projects. The local
`ros_pkgs/` tree (e.g. `heartbeat`) is still the place for packages that belong to the
deploy repo itself.

---

## The downstream contract (do this in your package repo)

Two steps make a LeagueRobotics repo part of the fleet:

1. **Add the GitHub topic `fleet-ros-package`** to the repo
   (repo page → ⚙️ next to *About* → *Topics*).

2. **Commit a `fleet.yaml` at the repo root:**

   ```yaml
   # fleet.yaml — declares the ROS 2 packages this repo ships to the LeagueRobotics fleet.
   version: 1
   ref: main                 # optional; git ref to deploy. Default: the repo's default branch.
   packages:
     - name: lidar_driver    # MUST match <name> in the package's package.xml
       path: lidar_driver    # path to the package dir within the repo. Default: "." (repo root)
       groups: [ros_nodes]   # which fleet nodes build it (see "Targeting" below)
     - name: lidar_msgs
       path: lidar_msgs
       groups: [ros_nodes]
   ```

That's it. Re-running the deploy picks up the repo, builds the packages on the targeted
nodes, and they land on every login shell's ROS path via the shared `/opt/ros_ws` overlay.

### Targeting (`groups`)

Each package names the **inventory groups** whose hosts should build it. Values are matched
against each host's Ansible `group_names`:

| `groups` value     | Builds on |
|--------------------|-----------|
| `all`              | every node in `ros_nodes` |
| `ros_nodes`        | every physical/VM ROS node |
| `raspberry_pis`    | the Raspberry Pis only |
| `<other group>`    | hosts in that inventory group |

A package whose groups match no host simply isn't built anywhere (and that's logged). A
repo carrying the topic but **no** `fleet.yaml` is skipped with a warning — the manifest is
required.

### Dependencies

Declare your ROS/system dependencies normally in each package's `package.xml`. The deploy
runs `rosdep install --from-paths src --ignore-src` before building, so anything resolvable
by rosdep is installed automatically.

---

## How it works (in ros-deploy)

```
collect (control machine)                    distribute (each node)
─────────────────────────                    ──────────────────────
scripts/collect_fleet_packages.py            roles/fleet_packages
  • search org for the topic                   • select packages whose groups
  • read each fleet.yaml                          match this host
  • pin each ref → commit SHA                   • rsync sources → /opt/ros_ws/src/<pkg>
  • vcs import → .fleet/staging/               • rosdep install
  • write .fleet/packages.lock.yml             • colcon build --packages-select …
```

- **`.fleet/staging/`** — full clones of the discovered repos (gitignored, throwaway).
- **`.fleet/packages.lock.yml`** — the generated, **committed** record of exactly what the
  fleet deploys: each package → repo → pinned SHA → source path → target groups. Review it
  in PRs; it's the audit trail and the input the Ansible role reads.

Nodes never talk to GitHub — the control machine clones, then rsyncs sources to nodes, the
same way `roles/heartbeat` ships the local package.

---

## Running it

```bash
# Full deploy (collection runs first, then distribution):
ansible-playbook playbooks/site.yml

# Just the fleet-package stage:
ansible-playbook playbooks/fleet_packages.yml

# Deploy from the committed lockfile without re-scanning GitHub (reproducible/offline):
ansible-playbook playbooks/fleet_packages.yml -e fleet_collect=false

# Discover + write the lockfile without cloning (quick check):
python3 scripts/collect_fleet_packages.py --dry-run
```

### Control-machine prerequisites

- **vcstool** — `pipx install vcstool` or `apt install python3-vcstool`
- **`gh` CLI** (recommended) — used for discovery/SHA resolution with higher rate limits;
  without it the script falls back to the unauthenticated public REST API (60 req/hr).

Config lives in `group_vars/all.yml`: `fleet_org` (`LeagueRobotics`), `fleet_topic`
(`fleet-ros-package`), `fleet_collect` (re-scan on each run; default `true`).

---

## Adding / removing a package

- **Add a package to an existing fleet repo** — add an entry to its `fleet.yaml`, push,
  re-deploy.
- **Add a new repo** — add the topic + `fleet.yaml`, push, re-deploy. No change needed in
  `ros-deploy`.
- **Remove a package** — drop it from `fleet.yaml` (or remove the topic from the repo) and
  re-deploy. The next collection regenerates the lockfile without it; the node's
  `colcon build` no longer selects it. (Its old `src/`/`install/` artifacts can be cleaned
  from `/opt/ros_ws` if desired.)
