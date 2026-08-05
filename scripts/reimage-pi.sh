#!/usr/bin/env bash
# scripts/reimage-pi.sh
# ──────────────────────────────────────────────────────────────────────────────
# Reimage a fleet Raspberry Pi: pick it from the inventory, flash a card, then
# (optionally) deploy it — the whole path from "bare SD card" to "working ROS
# node" with one command.
#
# This is a THIN WRAPPER around scripts/flash-pi-card.sh, which does all the
# actual work (image download, the destructive write, cloud-init pre-seed). The
# wrapper only adds the things that need the Ansible inventory:
#
#   • an interactive menu of the Pis, with each one's IP, model and live status
#   • -n / -i / --model filled in from inventory instead of hand-typed
#   • a preflight that catches a missing WIFI_MESH_PSK, key, or image up front
#   • an extra confirmation when the selected host is CURRENTLY RUNNING
#   • an offer to run `site.yml` once the freshly flashed Pi boots
#
# Why a wrapper and not a --pick flag on flash-pi-card.sh: that script is
# deliberately inventory-free and self-contained, so it keeps working with no
# Ansible installed and no parseable inventory. Keeping the picker separate means
# the diff to the destructive code is exactly zero.
#
# ── Safety ────────────────────────────────────────────────────────────────────
# This wrapper is ADDITIVE to flash-pi-card.sh's confirmations, never
# subtractive. It NEVER chooses the target disk for you: run it without -d and
# it prints the detect table and stops, so a human still eyeballs and confirms
# /dev/diskN. It only ever forwards a -d/--confirm pair that a human typed.
#
# ── Why ROS is not on the card ────────────────────────────────────────────────
# The card carries only the "prep" layer (hostname, IP, SSH, the ansible user).
# ROS install stays in Ansible (roles/ros) because install_kilted.yml is a
# procedure, not a package list — it queries the GitHub API for the current
# ros-apt-source release at run time, then pulls ~193 MB / 225 packages.
# Duplicating that into cloud-init would be a second source of truth that can't
# be re-run and whose failures are invisible on a Pi you can't SSH into yet.
# Instead this script offers to run site.yml for you once the Pi boots.
#
# ── Usage ─────────────────────────────────────────────────────────────────────
#   Pick a host, see what would happen, touch nothing:
#     ./scripts/reimage-pi.sh --dry-run
#
#   Pick from the menu, then flash a confirmed card:
#     ./scripts/reimage-pi.sh -d /dev/disk5 --confirm /dev/disk5
#
#   Skip the menu (still preflights and confirms):
#     ./scripts/reimage-pi.sh -n baldur -d /dev/disk5 --confirm /dev/disk5
#
# ── Options ───────────────────────────────────────────────────────────────────
#   -n NAME           Target this inventory host; skips the menu
#   --group GROUP     Inventory group to list           (default: raspberry_pis)
#   --model M         Override the model from inventory (3 | 4 | 5 | zero2w)
#   -d DEVICE         Target whole disk, e.g. /dev/disk5   (passed through)
#   --confirm DEVICE  Must EXACTLY match -d               (passed through)
#   --no-ping         Skip the reachability probe (faster menu)
#   --deploy          After flashing, wait for boot and run site.yml
#   --no-deploy       Never offer to run site.yml         (default: ask)
#   --force-live      Allow reimaging a live host without a TTY prompt
#   --dry-run         Print the flash-pi-card.sh command and exit
#   -h                Show this help and exit
#
#   Passed straight through to flash-pi-card.sh:
#     -U USER, --operator-key F, --password PW, --no-operator,
#     -m IMAGE, --no-download, --max-gb N
#
# Exit codes: 0 ok · 1 runtime error · 2 preflight failed (environment not ready)
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
FLASH="${SCRIPT_DIR}/flash-pi-card.sh"
ANSIBLE_PUBKEY="${REPO_DIR}/config/keys/ansible.pub"
ANSIBLE_KEY="${REPO_DIR}/config/files/ansible"

# Same vocabulary flash-pi-card.sh accepts for --model.
VALID_MODELS="3 4 5 zero2w"

# How long to wait for a freshly flashed Pi to boot and answer Ansible.
DEPLOY_POLL_SECS=15
DEPLOY_MAX_SECS=300

NAME=""
GROUP="raspberry_pis"
MODEL=""
DEVICE=""
CONFIRM=""
DO_PING=1
DEPLOY="ask"          # ask | yes | no
FORCE_LIVE=0
DRY_RUN=0
PASSTHRU=()

INVENTORY_JSON=""     # cached ansible-inventory output

usage() { grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \{0,1\}//'; exit 0; }
die()   { echo "ERROR: $*" >&2; exit 1; }
note()  { echo "==> $*"; }

# ── Argument parsing ──────────────────────────────────────────────────────────
# Long-option while/case loop, matching flash-pi-card.sh's style (not getopts,
# which can't do --long-options portably).
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n)              NAME="${2:?-n needs a value}"; shift 2 ;;
    --group)         GROUP="${2:?--group needs a value}"; shift 2 ;;
    --model)         MODEL="${2:?--model needs a value}"; shift 2 ;;
    -d)              DEVICE="${2:?-d needs a value}"; shift 2 ;;
    --confirm)       CONFIRM="${2:?--confirm needs a value}"; shift 2 ;;
    --no-ping)       DO_PING=0; shift ;;
    --deploy)        DEPLOY="yes"; shift ;;
    --no-deploy)     DEPLOY="no"; shift ;;
    --force-live)    FORCE_LIVE=1; shift ;;
    --dry-run)       DRY_RUN=1; shift ;;
    -h|--help)       usage ;;
    # Options we don't interpret, only forward. Validated against a known list
    # so a typo fails here with a clear message rather than surfacing as a
    # confusing "Unknown argument" from the inner script.
    -U|--operator-key|--password|-m|--max-gb)
                     PASSTHRU+=( "$1" "${2:?$1 needs a value}" ); shift 2 ;;
    --no-operator|--no-download)
                     PASSTHRU+=( "$1" ); shift ;;
    *)               die "Unknown argument: $1  (try -h)" ;;
  esac
done

# ── Inventory helpers ─────────────────────────────────────────────────────────

# Load the inventory ONCE and cache it. ansible-inventory is slow (~1-2s) and
# chatty on stderr, and we query it many times below.
load_inventory() {
  # cd into the repo so ansible.cfg's `inventory = inventory/hosts.ini` applies.
  INVENTORY_JSON="$(cd "$REPO_DIR" && ansible-inventory --list --limit "$GROUP" 2>/dev/null)" \
    || die "ansible-inventory failed — check inventory/hosts.ini parses."
  [[ -n "$INVENTORY_JSON" ]] || die "ansible-inventory returned nothing for group '$GROUP'."
}

# Hosts in the group, in inventory order.
inv_hosts() {
  jq -r --arg g "$GROUP" '.[$g].hosts[]?' <<<"$INVENTORY_JSON"
}

# inv_var <host> <key> — echoes the merged value, or nothing if unset.
# `// empty` matters: a host with no host_vars file yields null, and we want
# "unset" to be an empty string so callers can treat it as "ask".
inv_var() {
  jq -r --arg h "$1" --arg k "$2" '._meta.hostvars[$h][$k] // empty' <<<"$INVENTORY_JSON"
}

# True for the YAML/JSON spellings of boolean true. jq renders `true`, but a
# host_vars value written as `yes` or `True` reaches us verbatim, so accept all.
inv_is_true() {
  case "$(inv_var "$1" "$2")" in
    true|True|TRUE|yes|Yes|YES) return 0 ;;
    *)                          return 1 ;;
  esac
}

# A one-line hint of what the host does, from booleans already in the inventory.
# Cheaper and truer than a hand-maintained description field.
inv_role_note() {
  local h="$1" notes=()
  inv_is_true "$h" install_cameras         && notes+=("cameras")
  inv_is_true "$h" install_revhub          && notes+=("revhub")
  inv_is_true "$h" xdrive_run_driver       && notes+=("xdrive")
  inv_is_true "$h" xdrive_run_joy_to_twist && notes+=("teleop")
  inv_is_true "$h" configure_desktop       && notes+=("vnc")
  # ${arr[*]:-} on an empty array trips `set -u` in bash 3.2; guard the count.
  (( ${#notes[@]} )) || { echo ""; return 0; }
  local IFS=,; echo "${notes[*]}"
}

# Reachability. NOTE: macOS ping uses -t for timeout in SECONDS; -W is a
# per-packet wait in milliseconds. Don't copy the Linux `-W 1` idiom here.
probe_host() {
  ping -c1 -t1 "$1" >/dev/null 2>&1 && echo "UP" || echo "down"
}

# Resolve a hostname via LAN DNS — same order flash-pi-card.sh uses. Only used
# to CROSS-CHECK the inventory address, never as the primary source.
resolve_dns() {
  local name="$1" ip=""
  ip="$(dig +short "$name" A 2>/dev/null | grep -E '^([0-9]{1,3}\.){3}[0-9]{1,3}$' | head -1)"
  [[ -z "$ip" ]] && ip="$(dscacheutil -q host -a name "$name" 2>/dev/null | awk '/^ip_address/{print $2; exit}')"
  echo "$ip"
}

# ── Preflight ─────────────────────────────────────────────────────────────────
# Report EVERY problem at once rather than dying on the first, so the operator
# fixes their environment in one pass instead of playing whack-a-mole.
preflight() {
  local fails=0
  note "Preflight"

  [[ "$(uname -s)" == "Darwin" ]] || { echo "  FAIL  macOS only (uses diskutil)"; fails=1; }

  for c in ansible-inventory jq; do
    if command -v "$c" >/dev/null 2>&1; then
      echo "  ok    $c"
    else
      echo "  FAIL  $c not found  →  $([[ $c == jq ]] && echo 'brew install jq' || echo 'pipx install ansible')"
      fails=1
    fi
  done

  [[ -x "$FLASH" ]] || { echo "  FAIL  $FLASH missing or not executable"; fails=1; }

  # WiFi PSK. flash-pi-card.sh falls back to reading .env itself, so an unset
  # env var is NOT automatically fatal — distinguish the two cases rather than
  # crying wolf. Never print the value.
  if [[ -n "${WIFI_MESH_PSK:-}" ]]; then
    echo "  ok    WIFI_MESH_PSK (environment)"
  elif [[ -f "${REPO_DIR}/.env" ]] \
       && grep -q '^WIFI_MESH_PSK=.\+' "${REPO_DIR}/.env" 2>/dev/null \
       && ! grep -q '^WIFI_MESH_PSK=.*ENC\[' "${REPO_DIR}/.env" 2>/dev/null; then
    echo "  ok    WIFI_MESH_PSK (from .env)"
  else
    echo "  FAIL  WIFI_MESH_PSK unavailable"
    echo "        →  dotconfig load <deploy>; set -a; source .env; set +a"
    fails=1
  fi

  # The pubkey baked into the card. flash-pi-card.sh also checks, but late —
  # after the operator has already picked a host and a disk.
  if [[ -f "$ANSIBLE_PUBKEY" ]]; then
    echo "  ok    config/keys/ansible.pub"
  else
    echo "  FAIL  config/keys/ansible.pub missing (goes into the card)"
    fails=1
  fi

  # Only needed for the POST-flash deploy, so a warning rather than a failure.
  if [[ -f "$ANSIBLE_KEY" ]]; then
    echo "  ok    config/files/ansible"
  else
    echo "  WARN  config/files/ansible missing — flashing is fine, but the"
    echo "        site.yml step needs it  →  dotconfig key load ansible"
  fi

  # flash-pi-card.sh silently degrades to --no-operator without one; surface it
  # here so a card without your login isn't a surprise after the fact.
  local opkey=""
  for k in "$HOME/.ssh/id_ed25519.pub" "$HOME/.ssh/id_rsa.pub" "$HOME/.ssh/id_ecdsa.pub"; do
    [[ -f "$k" ]] && { opkey="$k"; break; }
  done
  if [[ -n "$opkey" ]]; then echo "  ok    operator key $(basename "$opkey")"
  else echo "  WARN  no ~/.ssh/id_*.pub — the human admin account will be skipped"; fi

  # Changes the expected runtime from ~4 min to ~15 min. Say so BEFORE a card
  # gets wiped, not after.
  if compgen -G "$HOME/Downloads/*raspi.img.xz" >/dev/null 2>&1; then
    echo "  ok    Ubuntu image cached in ~/Downloads"
  else
    echo "  INFO  no cached image — the flash will download ~1.2 GB first"
  fi

  # dd needs sudo. When an agent drives this, a mid-run password prompt stalls.
  sudo -n true 2>/dev/null \
    && echo "  ok    sudo (non-interactive)" \
    || echo "  INFO  sudo will prompt for a password during the write"

  (( fails == 0 )) || { echo; echo "Preflight failed. Fix the FAIL lines above." >&2; exit 2; }
  echo
}

# ── Menu ──────────────────────────────────────────────────────────────────────
print_menu() {
  local hosts=("$@") i=1
  printf "  %-3s %-9s %-16s %-6s %-7s %s\n" "#" "HOST" "IP" "MODEL" "STATUS" "ROLES"
  for h in "${hosts[@]}"; do
    local ip model status roles
    ip="$(inv_var "$h" ansible_host)"
    model="$(inv_var "$h" pi_model)"; model="${model:-?}"
    if (( DO_PING )); then status="$(probe_host "${ip:-$h}")"; else status="-"; fi
    roles="$(inv_role_note "$h")"
    printf "  %-3s %-9s %-16s %-6s %-7s %s\n" "$i)" "$h" "${ip:-<dns>}" "$model" "$status" "$roles"
    i=$((i+1))
  done
}

select_host() {
  local hosts=("$@")
  # A menu needs a human. Without a TTY the caller must pass -n.
  [[ -t 0 ]] || die "No TTY for the menu — pass -n NAME to select non-interactively."
  local choice
  while true; do
    read -rp "Select a host by number (q to quit): " choice
    [[ "$choice" == "q" ]] && { echo "Aborted."; exit 0; }
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#hosts[@]} )); then
      NAME="${hosts[$((choice-1))]}"
      return
    fi
    echo "  Enter a number between 1 and ${#hosts[@]}, or q."
  done
}

# ── Model resolution ──────────────────────────────────────────────────────────
# Never guess. A Pi 5 silently treated as a Pi 4 is exactly the class of mistake
# this picker exists to prevent, so an unknown model stops and asks.
resolve_model() {
  local host="$1"
  [[ -n "$MODEL" ]] && return                       # explicit --model wins
  MODEL="$(inv_var "$host" pi_model)"
  [[ -n "$MODEL" ]] && return

  [[ -t 0 ]] || die "No pi_model for '$host' and no TTY — pass --model."
  echo "No pi_model set for '$host' in inventory/host_vars/${host}.yml."
  while true; do
    read -rp "  Pi model [3|4|5|zero2w]: " MODEL
    [[ " $VALID_MODELS " == *" $MODEL "* ]] && break
    echo "  Must be one of: $VALID_MODELS"
  done
  # Close the gap permanently rather than asking again next time.
  echo
  echo "  Add this to inventory/host_vars/${host}.yml so it's remembered:"
  echo "      pi_model: \"${MODEL}\""
  echo
}

# ── Live-host guard ───────────────────────────────────────────────────────────
# Mirrors flash-pi-card.sh's --confirm idiom: type the exact name, not "y".
confirm_live_host() {
  local host="$1" ip="$2" roles typed
  [[ "$(probe_host "${ip:-$host}")" == "UP" ]] || return 0

  roles="$(inv_role_note "$host")"
  echo
  echo "  !!  ${host} (${ip}) is UP right now — it is a RUNNING fleet node."
  echo "      Reimaging replaces its OS. Anything not captured in Ansible"
  echo "      (local logs, uncommitted tweaks, /var/lib data) is LOST."
  [[ -n "$roles" ]] && echo "      It currently runs: ${roles}"
  echo "      Check that inventory/host_vars/${host}.yml is up to date first."
  echo

  if (( FORCE_LIVE )); then
    echo "  --force-live given; continuing."
    return 0
  fi
  [[ -t 0 ]] || die "'$host' is live and there's no TTY to confirm. Use --force-live if you mean it."

  read -rp "  Type the hostname to confirm you mean to reimage it: " typed
  [[ "$typed" == "$host" ]] || die "Got '$typed', expected '$host'. Aborted."
}

# ── Post-flash deploy ─────────────────────────────────────────────────────────
offer_deploy() {
  local host="$1" answer
  [[ "$DEPLOY" == "no" ]] && return 0

  if [[ "$DEPLOY" == "ask" ]]; then
    [[ -t 0 ]] || return 0
    echo
    read -rp "Wait for ${host} to boot and run site.yml now? [y/N]: " answer
    [[ "$answer" =~ ^[Yy]$ ]] || { print_manual_steps "$host"; return 0; }
  fi

  if [[ ! -f "$ANSIBLE_KEY" ]]; then
    echo "ERROR: ${ANSIBLE_KEY} missing — run: dotconfig key load ansible" >&2
    print_manual_steps "$host"
    return 0
  fi

  note "Waiting for ${host} to boot (up to $((DEPLOY_MAX_SECS/60)) min)..."
  local waited=0
  while (( waited < DEPLOY_MAX_SECS )); do
    if (cd "$REPO_DIR" && ansible "$host" -m ansible.builtin.ping >/dev/null 2>&1); then
      note "${host} is up. Running site.yml ..."
      # Inherit the environment so WIFI_MESH_PSK reaches roles/wifi.
      (cd "$REPO_DIR" && ansible-playbook playbooks/site.yml --limit "$host")
      return $?
    fi
    sleep "$DEPLOY_POLL_SECS"
    waited=$((waited + DEPLOY_POLL_SECS))
    printf "    ... %ss\n" "$waited"
  done

  # A timeout is not a flash failure — the card is already good.
  echo
  echo "Timed out waiting for ${host}. The card is fine; finish by hand:"
  print_manual_steps "$host"
  return 0
}

print_manual_steps() {
  local host="$1"
  echo
  echo "  Insert the card, power on, then:"
  echo "      ansible ${host} -m ansible.builtin.ping"
  echo "      ansible-playbook playbooks/site.yml --limit ${host}"
}

# ── Main ──────────────────────────────────────────────────────────────────────
preflight
load_inventory

# Read the host list portably. macOS ships bash 3.2, which has no `mapfile` /
# `readarray` — flash-pi-card.sh avoids bash 4 features for the same reason.
HOSTS=()
while IFS= read -r _h; do
  [[ -n "$_h" ]] && HOSTS+=( "$_h" )
done < <(inv_hosts)
(( ${#HOSTS[@]} > 0 )) || die "Group '$GROUP' has no hosts — check inventory/hosts.ini."

if [[ -n "$NAME" ]]; then
  # -n given: validate it against the inventory rather than trusting free text.
  printf '%s\n' "${HOSTS[@]}" | grep -qx "$NAME" \
    || die "'$NAME' is not in group '$GROUP'. Members: ${HOSTS[*]}"
else
  note "Raspberry Pis in group '$GROUP'"
  print_menu "${HOSTS[@]}"
  echo
  select_host "${HOSTS[@]}"
fi

IP="$(inv_var "$NAME" ansible_host)"
resolve_model "$NAME"

# Cross-check the inventory address against DNS. flash-pi-card.sh would use DNS
# if we passed no -i, while roles/wifi later pins ansible_host — so a divergence
# means the Pi boots at one address and Ansible moves it to another. Warn, but
# proceed with the inventory value, which is what Ansible will enforce.
if [[ -n "$IP" ]]; then
  DNS_IP="$(resolve_dns "$NAME")"
  if [[ -n "$DNS_IP" && "$DNS_IP" != "$IP" ]]; then
    echo "WARNING: inventory says ${NAME}=${IP} but DNS says ${DNS_IP}." >&2
    echo "         Using ${IP} (roles/wifi enforces ansible_host)." >&2
  fi
fi

echo
note "Target: ${NAME}  ip=${IP:-<dns lookup>}  model=Pi ${MODEL}"

confirm_live_host "$NAME" "$IP"

# Build the argv as an ARRAY, never a string — a --password containing spaces
# must survive intact.
CMD=( "$FLASH" -n "$NAME" --model "$MODEL" )
[[ -n "$IP" ]] && CMD+=( -i "$IP" )
[[ -n "$DEVICE" ]] && CMD+=( -d "$DEVICE" )
[[ -n "$CONFIRM" ]] && CMD+=( --confirm "$CONFIRM" )
(( ${#PASSTHRU[@]} )) && CMD+=( "${PASSTHRU[@]}" )

if (( DRY_RUN )); then
  echo
  note "Dry run — would execute:"
  printf '    '; printf '%q ' "${CMD[@]}"; echo
  exit 0
fi

# No device yet? Show the candidates and STOP. Choosing the disk is the one
# decision this wrapper must never make for the operator.
if [[ -z "$DEVICE" ]]; then
  echo
  note "Candidate cards (the PICK=YES row is a guess, not a decision):"
  "$FLASH" --detect
  echo
  echo "Confirm the correct disk, then re-run with it named twice:"
  printf '    '; printf '%q ' "${CMD[@]}"; printf -- '-d /dev/diskN --confirm /dev/diskN\n'
  exit 0
fi

# flash-pi-card.sh enforces this too; catching it here gives a clearer message
# before any of its own setup runs.
[[ "$DEVICE" == "$CONFIRM" ]] || die "--confirm ('$CONFIRM') must exactly match -d ('$DEVICE')."

echo
note "Flashing ..."
"${CMD[@]}"
FLASH_RC=$?
(( FLASH_RC == 0 )) || exit "$FLASH_RC"

offer_deploy "$NAME"
