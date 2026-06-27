#!/usr/bin/env bash
# scripts/flash-pi-card.sh
# ──────────────────────────────────────────────────────────────────────────────
# Flash Ubuntu 24.04 LTS (preinstalled server, arm64, Raspberry Pi) to an SD card
# and pre-seed cloud-init so the Pi boots FULLY ANSIBLE-READY — no manual
# bootstrap needed. The first boot configures only the "prep" layer:
#
#   • hostname                          (the name you give it)
#   • fleet WiFi  "Busboom Mesh"         static IP on wlan0 (eth0 = DHCP fallback)
#   • SSH enabled, host keys regenerated (unique per card)
#   • the `ansible` service user         + project pubkey + NOPASSWD sudo
#                                         (mirrors playbooks/bootstrap.yml)
#   • the `jtl` admin account            (shared school login + your personal key)
#
# It deliberately does NOT install ROS or any role — those stay in Ansible
# (`ansible-playbook playbooks/site.yml --limit <name>`) so reinstalls work.
#
# macOS only (uses diskutil). The image write is the only step that needs sudo.
#
# ── Usage ─────────────────────────────────────────────────────────────────────
#   List candidate cards (no writes — run this first):
#     ./scripts/flash-pi-card.sh --detect
#
#   Preview the cloud-init files without touching a card:
#     WIFI_MESH_PSK=… ./scripts/flash-pi-card.sh -n golem -i 192.168.1.173 \
#         --render-only /tmp/golem-boot
#
#   Flash (PSK loaded from dotconfig secrets; IP resolved from DNS by name):
#     dotconfig load <deploy>; set -a; source .env; set +a   # exports WIFI_MESH_PSK
#     ./scripts/flash-pi-card.sh -n golem --model 4 \
#         -d /dev/disk5 --confirm /dev/disk5
#
# ── Options ───────────────────────────────────────────────────────────────────
#   -n NAME           Hostname for the Pi                      (always required)
#   --model M         Pi model: 3 | 4 | 5 | zero2w            (required to flash)
#                     All are arm64 and share one image. The ORIGINAL Pi Zero W
#                     (ARMv6) can't run Ubuntu and is rejected — use a Zero 2 W.
#   -i IP             Static IPv4 for wlan0  (default: resolve NAME via DNS)
#   -d DEVICE         Target whole disk, e.g. /dev/disk5       (required to flash)
#   --confirm DEVICE  Must EXACTLY match -d (anti-footgun)     (required to flash)
#   -m IMAGE          Path to the .img or .img.xz   (else auto-find, then download)
#   -U USER           Human admin user  (default: jtl; --no-operator to skip)
#   --operator-key F  Public key for the human user
#                     (default: first of ~/.ssh/{id_ed25519,id_rsa,id_ecdsa}.pub)
#   --password PW     Set a password on the human user (console/serial recovery)
#   --max-gb N        Refuse devices larger than this          (default: 34)
#   --no-download     Don't auto-download an image (fail if none found locally)
#   --detect          List external disks and exit (no writes)
#   --render-only DIR Write the cloud-init files to DIR and exit (no device, no dd)
#   -h                Show this help and exit
#
# Network parameters below are pinned to the fleet (roles/wifi/defaults/main.yml).
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Fleet network constants (keep in sync with roles/wifi/defaults/main.yml) ──
WIFI_SSID="Busboom Mesh"
NET_PREFIX=21
NET_GW="192.168.1.254"
NET_DNS=("192.168.1.254" "8.8.8.8")

# ── Defaults ──────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ANSIBLE_PUBKEY="${REPO_DIR}/config/keys/ansible.pub"

NAME=""
MODEL=""
ARCH="arm64"          # set from --model; all fleet-capable Pis are arm64
IP=""
DEVICE=""
CONFIRM=""
IMAGE=""
OPERATOR="jtl"         # shared school admin account (override with -U)
OPERATOR_KEY=""        # empty => auto-detect from the candidates below
OPERATOR_PASSWORD=""
NO_OPERATOR=0
MAX_GB=34
DO_DETECT=0
NO_DOWNLOAD=0
RENDER_DIR=""

# The /ubuntu/ prefix matters: the bare /releases/24.04/release/ path
# intermittently serves the parent index instead of this directory (flaky
# cdimage backend), so discovery would find no image. This path is stable.
UBUNTU_RELEASE_URL="https://cdimage.ubuntu.com/ubuntu/releases/24.04/release/"

die()  { echo "ERROR: $*" >&2; exit 1; }
note() { echo "==> $*"; }
usage() {
  # Print only the leading header comment block (stop at the first code line),
  # so inline section/function comments don't leak into --help.
  awk 'NR==1 && /^#!/ {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "$0"
  exit 0
}

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n) NAME="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    -i) IP="$2"; shift 2 ;;
    -d) DEVICE="$2"; shift 2 ;;
    --confirm) CONFIRM="$2"; shift 2 ;;
    -m) IMAGE="$2"; shift 2 ;;
    -U) OPERATOR="$2"; shift 2 ;;
    --operator-key) OPERATOR_KEY="$2"; shift 2 ;;
    --password) OPERATOR_PASSWORD="$2"; shift 2 ;;
    --no-operator) NO_OPERATOR=1; shift ;;
    --max-gb) MAX_GB="$2"; shift 2 ;;
    --detect) DO_DETECT=1; shift ;;
    --render-only) RENDER_DIR="$2"; shift 2 ;;
    --no-download) NO_DOWNLOAD=1; shift ;;
    --download) shift ;;            # deprecated: downloading is now the default
    -h|--help) usage ;;
    *) die "Unknown argument: $1 (use -h for help)" ;;
  esac
done

[[ "$(uname -s)" == "Darwin" ]] || die "This script is macOS-only (uses diskutil)."
command -v diskutil >/dev/null || die "diskutil not found."

# ── Per-device facts (text-parse diskutil info) ───────────────────────────────
dev_bytes()    { diskutil info "$1" 2>/dev/null | sed -n 's/.*(\([0-9][0-9]*\) Bytes.*/\1/p' | head -1; }
dev_location() { diskutil info "$1" 2>/dev/null | awk -F: '/Device Location/{gsub(/^[ \t]+/,"",$2); print $2; exit}'; }
dev_name()     { diskutil info "$1" 2>/dev/null | awk -F: '/Device \/ Media Name/{gsub(/^[ \t]+/,"",$2); print $2; exit}'; }
gb()           { awk -v b="$1" 'BEGIN{ printf "%.1f", b/1000000000 }'; }

# Resolve a hostname to an IPv4 via the LAN DNS (used when -i is omitted).
resolve_ip() {
  local name="$1" ip=""
  ip="$(dig +short "$name" A 2>/dev/null | grep -E '^([0-9]{1,3}\.){3}[0-9]{1,3}$' | head -1)"
  [[ -z "$ip" ]] && ip="$(dscacheutil -q host -a name "$name" 2>/dev/null | awk '/^ip_address/{print $2; exit}')"
  [[ -z "$ip" ]] && ip="$(ping -c1 -t1 "$name" 2>/dev/null | sed -n 's/.*(\([0-9.]*\)).*/\1/p' | head -1)"
  echo "$ip"
}

# Map a Pi model to its Ubuntu architecture. Pi 3/4/5 and the Zero 2 W are all
# arm64 and share one image; the ORIGINAL Pi Zero W is ARMv6 (no Ubuntu support).
model_arch() {
  local m; m="$(echo "$1" | tr 'A-Z' 'a-z' | tr -d ' _-')"
  case "$m" in
    3|pi3|3b|3b+|3bplus|4|pi4|4b|5|pi5|zero2w|zero2|z2w|pizero2w) echo arm64 ;;
    zerow|zero|zw|pizerow|zerowh)
      die "The original Pi Zero W (ARMv6) can't run Ubuntu. Use a Pi Zero 2 W (--model zero2w), or flash Raspberry Pi OS for that board." ;;
    *) die "Unknown --model '$1'. Use one of: 3 | 4 | 5 | zero2w." ;;
  esac
}

# Read one KEY=VALUE from an env file as a LITERAL (no shell evaluation). Strips
# one surrounding pair of single/double quotes; anything inside (including a
# quote) is kept verbatim — so a PSK that contains a quote survives even though
# the file isn't valid to `source`.
read_env_value() {
  local file="$1" key="$2" raw
  raw="$(grep -E "^(export[[:space:]]+)?${key}=" "$file" | tail -1)" || return 1
  [[ -n "$raw" ]] || return 1
  raw="${raw#*=}"
  if [[ ${#raw} -ge 2 && ${raw:0:1} == "'" && ${raw: -1} == "'" ]]; then
    raw="${raw:1:${#raw}-2}"
  elif [[ ${#raw} -ge 2 && ${raw:0:1} == '"' && ${raw: -1} == '"' ]]; then
    raw="${raw:1:${#raw}-2}"
  fi
  printf '%s' "$raw"
}

# ── --detect : list external physical disks, flag likely SD cards ─────────────
detect() {
  note "External physical disks (likely SD card = within --max-gb ${MAX_GB} and not internal):"
  printf "  %-12s %-10s %-7s %s\n" DEVICE SIZE PICK "MEDIA NAME"
  local found=0
  while read -r dev; do
    [[ -n "$dev" ]] || continue
    found=1
    local bytes loc nm pick
    bytes="$(dev_bytes "$dev")"; [[ -n "$bytes" ]] || bytes=0
    loc="$(dev_location "$dev")"
    nm="$(dev_name "$dev")"
    pick="no"
    if [[ "$loc" == "External" ]] && (( bytes > 1000000000 )) && \
       awk -v b="$bytes" -v m="$MAX_GB" 'BEGIN{exit !(b <= m*1000000000)}'; then
      pick="YES"
    fi
    printf "  %-12s %-10s %-7s %s\n" "$dev" "$(gb "$bytes") GB" "$pick" "${nm:-?}"
  done < <(diskutil list external physical 2>/dev/null | grep -oE '^/dev/disk[0-9]+')
  (( found )) || echo "  (none attached)"
  echo
  echo "Verify the device with: diskutil info <device>"
}

if (( DO_DETECT )); then detect; exit 0; fi

# ── Resolve the Ubuntu image ──────────────────────────────────────────────────
resolve_image() {
  if [[ -n "$IMAGE" ]]; then
    [[ -f "$IMAGE" ]] || die "Image not found: $IMAGE"
    return
  fi
  local cand
  cand="$(ls -t \
    "${HOME}"/Downloads/*preinstalled-server-${ARCH}+raspi.img.xz \
    "${HOME}"/Downloads/*${ARCH}+raspi.img.xz \
    ./*${ARCH}+raspi.img.xz 2>/dev/null | head -1 || true)"
  if [[ -n "$cand" ]]; then note "Using local image: ${cand}"; IMAGE="$cand"; return; fi

  (( NO_DOWNLOAD )) && die "No local ${ARCH} image found and --no-download set. Pass -m <file>."
  command -v curl >/dev/null || die "curl is needed to download the image."
  note "Finding latest 24.04 ${ARCH} raspi server image ..."

  # Keep fetch and grep separate (each `|| true`) so a no-match never trips
  # `set -e` + `pipefail` silently. Retry in case the cdimage backend serves a
  # stale/parent listing on a given hit.
  local index fname="" try
  for try in 1 2 3 4 5; do
    index="$(curl -fsSL "$UBUNTU_RELEASE_URL" 2>/dev/null || true)"
    fname="$(printf '%s\n' "$index" \
      | grep -oE "ubuntu-24\.04[^\"]*preinstalled-server-${ARCH}\+raspi\.img\.xz" \
      | sort -u | tail -1 || true)"
    [[ -n "$fname" ]] && break
    sleep 1
  done
  [[ -n "$fname" ]] || die \
    "Couldn't find a ${ARCH} raspi image at ${UBUNTU_RELEASE_URL} (network down, or the mirror is flaky). Pass -m <file.img.xz> to use a local image."

  IMAGE="${HOME}/Downloads/${fname}"
  if [[ -f "$IMAGE" ]]; then
    note "Using cached download: ${IMAGE}"
  else
    note "Downloading ${fname} (~1.2 GB; cached in ~/Downloads for reuse) ..."
    # Stage to .partial and move on success, so an interrupted download is never
    # mistaken for a cached image on the next run.
    curl -fL --retry 3 "${UBUNTU_RELEASE_URL}${fname}" -o "${IMAGE}.partial" \
      || { rm -f "${IMAGE}.partial"; die "Download failed. Re-run, or fetch ${UBUNTU_RELEASE_URL}${fname} by hand and pass -m."; }
    mv "${IMAGE}.partial" "$IMAGE"
  fi
}

# ── Write the three cloud-init files into a boot dir (mounted card or any dir) ─
write_cloudinit() {
  local boot="$1"
  [[ -d "$boot" ]] || die "Boot dir does not exist: $boot"

  # Escape the PSK for safe embedding inside a double-quoted YAML scalar.
  local esc_psk="${WIFI_MESH_PSK//\\/\\\\}"; esc_psk="${esc_psk//\"/\\\"}"

  note "Writing network-config (netplan v2) ..."
  cat > "${boot}/network-config" <<NETCFG
version: 2
ethernets:
  eth0:
    dhcp4: true
    optional: true
wifis:
  wlan0:
    dhcp4: false
    optional: true
    access-points:
      "${WIFI_SSID}":
        password: "${esc_psk}"
    addresses:
      - ${IP}/${NET_PREFIX}
    routes:
      - to: default
        via: ${NET_GW}
    nameservers:
      addresses: [${NET_DNS[0]}, ${NET_DNS[1]}]
NETCFG

  note "Writing meta-data ..."
  cat > "${boot}/meta-data" <<METADATA
instance-id: ${NAME}-$(date +%s)
local-hostname: ${NAME}
METADATA

  note "Writing user-data (cloud-init) ..."
  {
    echo "#cloud-config"
    echo "hostname: ${NAME}"
    echo "prefer_fqdn_over_hostname: false"
    echo "manage_etc_hosts: localhost"
    echo "ssh_pwauth: false"
    echo "ssh_deletekeys: true"
    echo "users:"
    echo "  - name: ansible"
    echo "    gecos: \"Ansible automation account\""
    echo "    shell: /bin/bash"
    echo "    lock_passwd: true"
    echo "    sudo: \"ALL=(ALL) NOPASSWD:ALL\""
    echo "    ssh_authorized_keys:"
    echo "      - \"${ANSIBLE_KEY_LINE}\""
    if (( ! NO_OPERATOR )); then
      echo "  - name: ${OPERATOR}"
      echo "    gecos: \"${OPERATOR} (fleet admin)\""
      echo "    shell: /bin/bash"
      echo "    groups: [adm, sudo]"
      echo "    sudo: \"ALL=(ALL) NOPASSWD:ALL\""
      if [[ -n "$OPERATOR_PASSWORD" ]]; then
        echo "    lock_passwd: false"
      else
        echo "    lock_passwd: true"
      fi
      echo "    ssh_authorized_keys:"
      echo "      - \"${OPERATOR_KEY_LINE}\""
    fi
    if [[ -n "$OPERATOR_PASSWORD" && $NO_OPERATOR -eq 0 ]]; then
      echo "chpasswd:"
      echo "  expire: false"
      echo "  users:"
      echo "    - name: ${OPERATOR}"
      echo "      password: \"${OPERATOR_PASSWORD}\""
      echo "      type: text"
    fi
    echo "runcmd:"
    echo "  - [ systemctl, enable, --now, ssh ]"
  } > "${boot}/user-data"
}

# ── Common preconditions (needed to render OR flash) ──────────────────────────
[[ -n "$NAME" ]] || die "Missing -n NAME (hostname)."
echo "$NAME" | grep -qE '^[a-z][a-z0-9-]*$' || die "Invalid hostname: $NAME (use [a-z][a-z0-9-]*)."

# IP: use -i if given, else resolve the name from DNS (static lease lives there).
if [[ -z "$IP" ]]; then
  note "No -i given; resolving '${NAME}' via DNS ..."
  IP="$(resolve_ip "$NAME")"
  [[ -n "$IP" ]] || die "Could not resolve '${NAME}' to an IP. Add it to DNS or pass -i."
  note "Resolved ${NAME} -> ${IP}"
fi
echo "$IP" | grep -qE '^([0-9]{1,3}\.){3}[0-9]{1,3}$' || die "Invalid IP: $IP"

# Validate the model early (also sets ARCH); --model is required at flash time.
[[ -n "$MODEL" ]] && ARCH="$(model_arch "$MODEL")"

# WiFi PSK from the environment (dotconfig secret) — never hard-coded in the repo.
# Prefer sourcing the repo .env (the documented dotconfig flow — handles proper
# shell quoting). If the file is malformed (e.g. dotconfig left a value with an
# un-escaped quote, so `source` aborts), fall back to reading it literally so a
# flash still succeeds.
if [[ -z "${WIFI_MESH_PSK:-}" && -f "${REPO_DIR}/.env" ]]; then
  note "WIFI_MESH_PSK not in env; loading ${REPO_DIR}/.env ..."
  WIFI_MESH_PSK="$(set +e; set -a; source "${REPO_DIR}/.env" 2>/dev/null; printf '%s' "${WIFI_MESH_PSK-}")"
  [[ -n "${WIFI_MESH_PSK:-}" ]] || WIFI_MESH_PSK="$(read_env_value "${REPO_DIR}/.env" WIFI_MESH_PSK || true)"
fi
[[ -n "${WIFI_MESH_PSK:-}" ]] || die \
  "WIFI_MESH_PSK not set and not found in ${REPO_DIR}/.env. Run: dotconfig load <deploy>"

# SSH keys.
[[ -f "$ANSIBLE_PUBKEY" ]] || die "Ansible pubkey missing: $ANSIBLE_PUBKEY"
ANSIBLE_KEY_LINE="$(cat "$ANSIBLE_PUBKEY")"
OPERATOR_KEY_LINE=""
if (( ! NO_OPERATOR )); then
  if [[ -z "$OPERATOR_KEY" ]]; then
    for k in "${HOME}/.ssh/id_ed25519.pub" \
             "${HOME}/.ssh/id_rsa.pub" \
             "${HOME}/.ssh/id_ecdsa.pub"; do
      [[ -f "$k" ]] && OPERATOR_KEY="$k" && break
    done
  fi
  if [[ -n "$OPERATOR_KEY" && -f "$OPERATOR_KEY" ]]; then
    OPERATOR_KEY_LINE="$(cat "$OPERATOR_KEY")"
    note "Human admin '${OPERATOR}' will use key: ${OPERATOR_KEY}"
  else
    echo "WARNING: no operator pubkey found (~/.ssh/{raspi-cluster_ed25519,id_ed25519,id_rsa}.pub); skipping the human user. Pass --operator-key to add one." >&2
    NO_OPERATOR=1
  fi
fi

# ── Render-only mode: write the files to a dir and stop (no device, no dd) ────
if [[ -n "$RENDER_DIR" ]]; then
  mkdir -p "$RENDER_DIR"
  write_cloudinit "$RENDER_DIR"
  note "Rendered cloud-init into ${RENDER_DIR} (network-config, meta-data, user-data)."
  exit 0
fi

# ── Flash preconditions ───────────────────────────────────────────────────────
[[ -n "$DEVICE" ]] || die "Missing -d DEVICE (run --detect first)."
[[ -n "$MODEL" ]]  || die "Missing --model (3|4|5|zero2w) — needed to pick the image."

# Hard anti-footgun: --confirm must exactly equal -d (or, if a TTY, prompt).
if [[ -z "$CONFIRM" ]]; then
  if [[ -t 0 ]]; then
    diskutil info "$DEVICE" 2>/dev/null | grep -E 'Device Location|Disk Size|Media Name' || true
    read -rp "Type the device to confirm ERASING it (e.g. $DEVICE): " CONFIRM
  else
    die "Refusing to flash without --confirm <device> (must match -d) in non-interactive mode."
  fi
fi
[[ "$CONFIRM" == "$DEVICE" ]] || die "--confirm ($CONFIRM) does not match -d ($DEVICE). Aborting."

# Device must be external and within the size cap — never an internal/large disk.
LOC="$(dev_location "$DEVICE")"
BYTES="$(dev_bytes "$DEVICE")"; [[ -n "$BYTES" ]] || die "Could not read size of $DEVICE."
[[ "$LOC" == "External" ]] || die "$DEVICE is '$LOC', not External. Refusing (safety)."
awk -v b="$BYTES" -v m="$MAX_GB" 'BEGIN{exit !(b <= m*1000000000)}' \
  || die "$DEVICE is $(gb "$BYTES") GB, larger than --max-gb ${MAX_GB}. Refusing (safety)."

resolve_image
RAW="${DEVICE/\/dev\/disk//dev/rdisk}"   # /dev/disk5 -> /dev/rdisk5 (faster writes)

# ── Summary ───────────────────────────────────────────────────────────────────
cat <<SUMMARY

  ──────────────────────────────────────────────────────────────
   FLASH PLAN
  ──────────────────────────────────────────────────────────────
   Image      : ${IMAGE}
   Target     : ${DEVICE}  (raw: ${RAW})  $(gb "$BYTES") GB, ${LOC}
   Hostname   : ${NAME}
   Model      : Pi ${MODEL} (${ARCH})
   Static IP  : ${IP}/${NET_PREFIX}  via ${NET_GW}  on "${WIFI_SSID}" (wlan0)
   Service usr: ansible (NOPASSWD sudo, project key)
   Human user : $([[ $NO_OPERATOR -eq 1 ]] && echo "(none)" || echo "${OPERATOR} (your key${OPERATOR_PASSWORD:+ + password})")
  ──────────────────────────────────────────────────────────────
   ⚠  EVERYTHING ON ${DEVICE} WILL BE ERASED.
  ──────────────────────────────────────────────────────────────
SUMMARY

# ── Flash ─────────────────────────────────────────────────────────────────────
note "Unmounting ${DEVICE} ..."
diskutil unmountDisk "$DEVICE"

note "Writing image (needs sudo; this takes a few minutes)..."
if [[ "$IMAGE" == *.xz ]]; then
  if command -v pv >/dev/null; then
    xz -dc "$IMAGE" | pv | sudo dd of="$RAW" bs=4m
  else
    xz -dc "$IMAGE" | sudo dd of="$RAW" bs=4m
  fi
else
  if command -v pv >/dev/null; then
    pv "$IMAGE" | sudo dd of="$RAW" bs=4m
  else
    sudo dd if="$IMAGE" of="$RAW" bs=4m
  fi
fi
sync

# ── Mount the boot partition and write cloud-init ─────────────────────────────
note "Mounting the boot partition ..."
diskutil mountDisk "$DEVICE" >/dev/null 2>&1 || true
BOOT=""
for _ in $(seq 1 15); do
  for cand in /Volumes/system-boot /Volumes/bootfs /Volumes/boot; do
    [[ -d "$cand" && -f "$cand/config.txt" ]] && BOOT="$cand" && break
  done
  [[ -n "$BOOT" ]] && break
  sleep 1
done
[[ -n "$BOOT" ]] || die "Could not find the mounted boot partition (looked for /Volumes/system-boot)."
note "Boot partition: ${BOOT}"

write_cloudinit "$BOOT"

sync
note "Ejecting ${DEVICE} ..."
diskutil eject "$DEVICE" >/dev/null 2>&1 || true

cat <<DONE

✅ Done. Put the card in ${NAME} and power on. After ~1–2 min it should be at
   ${IP} (or via ethernet DHCP). Then:

     ansible ${NAME} -m ansible.builtin.ping        # confirm reachable
     ansible-playbook playbooks/site.yml --limit ${NAME}

DONE
