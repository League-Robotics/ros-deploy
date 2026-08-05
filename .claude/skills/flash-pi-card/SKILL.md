---
name: flash-pi-card
description: >-
  Flash Ubuntu 24.04 LTS onto an SD card for a NEW Raspberry Pi fleet node,
  pre-seeded with cloud-init so it boots fully Ansible-ready (hostname, Busboom
  Mesh static WiFi IP, SSH on, the `ansible` service user + key + NOPASSWD sudo,
  plus a human admin user). Use when the user wants to prep / flash / image an SD
  card for a new Pi, or "set up a new card". macOS only. Wraps
  scripts/flash-pi-card.sh.
---

# Flash a Pi SD card (cloud-init pre-seed)

`scripts/flash-pi-card.sh` does the work; this skill is the **interaction
protocol**. Flashing is destructive, so the human-in-the-loop confirmation steps
below are mandatory — do not skip them.

The card boots with only the **prep layer** baked in (hostname, WiFi static IP,
SSH, `ansible` user — mirrors `playbooks/bootstrap.yml`). ROS and all roles stay
in Ansible (`site.yml`) so reinstalls keep working. After first boot the
`ansible` user already exists, so **skip `bootstrap-ansible-user.sh`** and go
straight to `site.yml`.

## Procedure

0. **Existing fleet host? Start with the picker.** For a Pi already in the
   inventory, `./scripts/reimage-pi.sh` lists the Pis with their IP, model and
   live status, and fills in `-n`, `-i` and `--model` from `host_vars` so none
   of it is hand-typed. It also preflights the environment (PSK, keys, image
   cache) and, after flashing, offers to wait for boot and run `site.yml`.
   Use `flash-pi-card.sh` directly for a host that is NOT yet in the inventory.

   **The picker does not replace steps 1-2.** Run it without `-d` and it prints
   the detect table and stops — it never selects `/dev/diskN` for you. You still
   show the candidate to the user and get explicit confirmation, then re-run
   with `-d` and `--confirm`. The picker is additive to the confirmations below,
   never a substitute for them.

1. **Detect the card.** Ask the user to insert it, then:
   `./scripts/flash-pi-card.sh --detect`
   The row marked `PICK=YES` is the likely card (external, ≤ `--max-gb` 34, so a
   32 GB card at ~31.9 GB qualifies; 67 MB micro:bits and big SSDs are excluded).

2. **Confirm the device with the user — REQUIRED.** Show the candidate
   (`/dev/diskN`, size, media name) and have them explicitly confirm it is the
   right one. Never pick silently. If two externals look plausible, ask. This is
   the main safeguard against erasing the wrong disk.

3. **Get name + model; everything else, ASK — don't assume.** You always need:
   - `-n <name>` — hostname (lowercase `[a-z][a-z0-9-]*`).
   - `--model <3|4|5|zero2w>` — the Pi model. All four are arm64 and share one
     image. The ORIGINAL Pi Zero W (ARMv6) cannot run Ubuntu — if the user says
     "Zero W", confirm they mean a Zero **2** W (the script rejects the original).
   - The **IP is looked up from DNS by name** automatically (the host has a
     static lease there); pass `-i` only to override.

   For any option the user did NOT specify, ask before flashing rather than
   guessing: the human admin user (default `-U jtl` — the shared school account —
   keyed with your auto-detected `~/.ssh/id_rsa.pub`), a recovery `--password`,
   `--no-operator` for a service-only card, or `--max-gb`. The Ubuntu image is
   downloaded automatically (latest 24.04 arm64, cached in `~/Downloads`); pass
   `-m <file>` for a local image or `--no-download` to forbid fetching.

4. **Ensure the WiFi PSK is loaded.** The script needs `$WIFI_MESH_PSK` (the
   Busboom Mesh key) — it is never stored in the repo. Note it falls back to
   reading `.env` from the repo root, so an unset environment variable is NOT
   automatically fatal; `reimage-pi.sh`'s preflight reports which source it
   found (`ok WIFI_MESH_PSK (from .env)`). If genuinely unavailable, have the
   user run:
   `dotconfig load <deploy>; set -a; source .env; set +a`
   (Optional sanity check before flashing — render without touching the card:
   `./scripts/flash-pi-card.sh -n <name> -i <ip> --render-only /tmp/<name>-boot`
   then read the three files.)

5. **Flash.** Pass `-d` AND a matching `--confirm` (the script refuses to write
   otherwise, and hard-refuses internal/oversize disks):
   ```
   ./scripts/flash-pi-card.sh -n <name> --model <3|4|5|zero2w> -d /dev/diskN --confirm /dev/diskN
   ```
   The image write (`dd`) needs **sudo**. First test `sudo -n true`: if it
   succeeds, run the script directly. If it can't get a password
   non-interactively, hand the user the exact command to run in their terminal
   (the only line needing sudo is the `dd`). The write takes a few minutes — use
   a long Bash timeout (~600000 ms) or run it in the background.

6. **After flashing.** Add the host to the repo if it's new
   (`inventory/hosts.ini` under `[ros_nodes]` + `[raspberry_pis]`, and a
   `host_vars/<name>.yml` if it needs overrides). Tell the user to insert the
   card and power on. Then verify and deploy:
   ```
   ansible <name> -m ansible.builtin.ping          # reachable as the ansible user
   ansible-playbook playbooks/site.yml --limit <name>
   ```
   `reimage-pi.sh` offers to do this waiting-and-deploying for you.

   **ROS is intentionally NOT on the card.** `roles/ros/tasks/install_kilted.yml`
   is a procedure, not a package list — it queries the GitHub API for the current
   `ros-apt-source` release at run time, then pulls ~193 MB / 225 packages. Baking
   that into cloud-init would create a second source of truth that cannot be
   re-run and whose failures are invisible on a Pi nobody can SSH into yet. Do not
   propose adding it; run `site.yml` instead.

## Safety notes

- The script refuses any device that is internal or larger than `--max-gb`
  (default 34) — but YOU still confirm the exact `/dev/diskN` with the user.
- `--confirm` must equal `-d` exactly; mismatches abort.
- Everything on the target device is erased. Say so before flashing.
- **Reimaging a host that is currently `UP` destroys a running fleet node.**
  `reimage-pi.sh` shows live status in the menu and makes you type the hostname
  to confirm (not `y`) — but you must still raise it with the user explicitly.
  Check that `host_vars/<name>.yml` captures the node's config before wiping it.
- The PSK and (if used) the recovery password are written in cleartext onto the
  card's FAT boot partition — expected, but don't echo them into chat/logs.
