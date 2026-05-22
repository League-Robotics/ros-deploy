#!/usr/bin/env bash
# scripts/set-static-ip.sh
# ──────────────────────────────────────────────────────────────────────────────
# Configure a static IP on a remote Ubuntu host via SSH + NetworkManager.
#
# The script SSHes in as a regular user, auto-detects the active interface,
# current gateway, and subnet mask, then applies the requested static address
# using `nmcli` (passwordless sudo is NOT required — the script prompts for the
# remote sudo password and passes it via `sudo -S`).
#
# Usage:
#   ./scripts/set-static-ip.sh [OPTIONS] <host>
#
# Options:
#   -u USER   SSH login user           (default: $USER)
#   -k KEY    Path to SSH private key  (default: SSH agent)
#   -i IP     Static IP to assign      (will prompt if omitted)
#   -h        Show this help and exit
#
# Examples:
#   ./scripts/set-static-ip.sh agony.local
#   ./scripts/set-static-ip.sh -u eric -i 192.168.1.11 agony.local
#   ./scripts/set-static-ip.sh -u eric -k ~/.ssh/id_ed25519 -i 192.168.1.11 agony.local
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
SSH_USER="${USER}"
SSH_KEY=""
STATIC_IP=""
TARGET_HOST=""

# ── Helpers ───────────────────────────────────────────────────────────────────
usage() {
  grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \{0,1\}//'
  exit 0
}

die() { echo "ERROR: $*" >&2; exit 1; }

# ── Argument parsing ──────────────────────────────────────────────────────────
while getopts ":u:k:i:h" opt; do
  case "${opt}" in
    u) SSH_USER="${OPTARG}" ;;
    k) SSH_KEY="${OPTARG}" ;;
    i) STATIC_IP="${OPTARG}" ;;
    h) usage ;;
    :) die "Option -${OPTARG} requires an argument." ;;
    \?) die "Unknown option: -${OPTARG}" ;;
  esac
done
shift $((OPTIND - 1))

[[ $# -ge 1 ]] || { echo "Usage: $0 [OPTIONS] <host>"; exit 1; }
TARGET_HOST="$1"

# ── Build SSH args ────────────────────────────────────────────────────────────
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o BatchMode=yes)
[[ -n "${SSH_KEY}" ]] && SSH_OPTS+=(-i "${SSH_KEY}")

ssh_run() {
  ssh "${SSH_OPTS[@]}" "${SSH_USER}@${TARGET_HOST}" "$@"
}

# ── Verify connectivity ───────────────────────────────────────────────────────
echo "==> Checking SSH connectivity to ${TARGET_HOST} as ${SSH_USER}..."
ssh_run true || die "Cannot reach ${TARGET_HOST}. Check host/user/key and try again."

# ── Prompt for static IP if not supplied ─────────────────────────────────────
if [[ -z "${STATIC_IP}" ]]; then
  echo ""
  read -rp "Static IP to assign (e.g. 192.168.1.11): " STATIC_IP
fi
[[ -n "${STATIC_IP}" ]] || die "No IP address provided."

# Basic format check
if ! echo "${STATIC_IP}" | grep -qE '^([0-9]{1,3}\.){3}[0-9]{1,3}$'; then
  die "Invalid IP address: ${STATIC_IP}"
fi

# ── Prompt for sudo password ──────────────────────────────────────────────────
echo ""
read -rsp "Sudo password for ${SSH_USER}@${TARGET_HOST}: " SUDO_PASS
echo ""

# ── Discover current network settings ────────────────────────────────────────
echo "==> Detecting network configuration on ${TARGET_HOST}..."

# Active default interface
IFACE=$(ssh_run "ip route show default" | awk '/^default/ {print $5; exit}')
[[ -n "${IFACE}" ]] || die "Could not detect default network interface."

# Gateway
GW=$(ssh_run "ip route show default" | awk '/^default/ {print $3; exit}')
[[ -n "${GW}" ]] || die "Could not detect default gateway."

# Prefix length (e.g. 21 from "192.168.1.227/21")
PREFIX=$(ssh_run "ip addr show dev ${IFACE}" \
  | awk '/inet / {split($2, a, "/"); print a[2]; exit}')
[[ -n "${PREFIX}" ]] || PREFIX=24

# NetworkManager connection name for this interface
NM_CON=$(echo "${SUDO_PASS}" | ssh "${SSH_OPTS[@]}" "${SSH_USER}@${TARGET_HOST}" \
  "sudo -S nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null" \
  | awk -F: -v dev="${IFACE}" '$2==dev {print $1; exit}')
[[ -n "${NM_CON}" ]] || die "No active NetworkManager connection found for ${IFACE}."

echo "    Interface : ${IFACE}"
echo "    NM conn   : ${NM_CON}"
echo "    Gateway   : ${GW}"
echo "    Prefix    : /${PREFIX}"
echo "    Static IP : ${STATIC_IP}/${PREFIX}"
echo ""

# ── Confirmation ──────────────────────────────────────────────────────────────
read -rp "Apply this configuration? [y/N] " CONFIRM
[[ "$(echo "${CONFIRM}" | tr '[:upper:]' '[:lower:]')" == "y" ]] || { echo "Aborted."; exit 0; }

# ── Apply static IP ───────────────────────────────────────────────────────────
echo "==> Applying static IP — connection will drop briefly..."

ssh "${SSH_OPTS[@]}" "${SSH_USER}@${TARGET_HOST}" bash <<REMOTE
set -euo pipefail
echo "${SUDO_PASS}" | sudo -S nmcli connection modify "${NM_CON}" \
  ipv4.method    manual \
  ipv4.addresses "${STATIC_IP}/${PREFIX}" \
  ipv4.gateway   "${GW}" \
  ipv4.dns       "8.8.8.8,8.8.4.4"
echo "${SUDO_PASS}" | sudo -S nmcli connection up "${NM_CON}" >/dev/null
REMOTE

# ── Verify ────────────────────────────────────────────────────────────────────
echo "==> Waiting for host to come up at ${STATIC_IP}..."
for i in $(seq 1 15); do
  sleep 2
  if ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=3 \
       ${SSH_KEY:+-i "${SSH_KEY}"} "${SSH_USER}@${STATIC_IP}" true 2>/dev/null; then
    echo "==> Success! ${TARGET_HOST} is now reachable at ${STATIC_IP}."
    exit 0
  fi
  echo "    ... attempt ${i}/15"
done

echo ""
echo "WARNING: Could not verify connectivity at ${STATIC_IP} after 30 s."
echo "         The change may still have applied — try: ssh ${SSH_USER}@${STATIC_IP}"
