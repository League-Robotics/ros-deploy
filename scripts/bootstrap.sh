#!/usr/bin/env bash
# scripts/bootstrap.sh
# ──────────────────────────────────────────────────────────────────────────────
# One-time bootstrap for a freshly provisioned Ubuntu host.
# Only does what can't be delegated to Ansible:
#
#   1. Waits for SSH to come up, printing console instructions if it's not up.
#   2. Sets up passwordless sudo for the connecting user.
#   3. Optionally sets a static IP (via scripts/set-static-ip.sh).
#   4. Runs scripts/bootstrap-ansible-user.sh to install the ansible service
#      account and its SSH key.
#
# Everything else (window manager, VNC, ROS, etc.) is handled by Ansible.
#
# Usage:
#   ./scripts/bootstrap.sh [OPTIONS] [user@]<host>
#
# Options:
#   -k KEY    Path to SSH private key  (default: first key found in ~/.ssh)
#   -i IP     Static IP to assign      (will prompt if you say yes to static IP)
#   -h        Show this help and exit
#
# Examples:
#   ./scripts/bootstrap.sh eric@torture.local
#   ./scripts/bootstrap.sh eric@192.168.1.12
#   ./scripts/bootstrap.sh -i 192.168.1.12 eric@torture.local
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Defaults ──────────────────────────────────────────────────────────────────
SSH_USER=""
SSH_KEY=""
STATIC_IP=""
TARGET_HOST=""

for _candidate in ~/.ssh/id_ed25519 ~/.ssh/id_rsa ~/.ssh/id_ecdsa; do
  if [[ -f "${_candidate}" ]]; then
    SSH_KEY="${_candidate}"
    break
  fi
done

# ── Helpers ───────────────────────────────────────────────────────────────────
usage() {
  grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \{0,1\}//'
  exit 0
}

die() { echo "ERROR: $*" >&2; exit 1; }

ssh_run() {
  sshpass -p "${SSH_PASS}" \
    ssh -o StrictHostKeyChecking=accept-new \
        -o BatchMode=no \
        -o ConnectTimeout=5 \
        ${SSH_KEY:+-i "${SSH_KEY}"} \
        "${SSH_USER}@${TARGET_HOST}" "$@"
}

ssh_reachable() {
  sshpass -p "${SSH_PASS}" \
    ssh -o StrictHostKeyChecking=accept-new \
        -o BatchMode=no \
        -o ConnectTimeout=5 \
        ${SSH_KEY:+-i "${SSH_KEY}"} \
        "${SSH_USER}@${TARGET_HOST}" true 2>/dev/null
}

# ── Argument parsing ──────────────────────────────────────────────────────────
while getopts ":k:i:h" opt; do
  case "${opt}" in
    k) SSH_KEY="${OPTARG}" ;;
    i) STATIC_IP="${OPTARG}" ;;
    h) usage ;;
    :) die "Option -${OPTARG} requires an argument." ;;
    \?) die "Unknown option: -${OPTARG}" ;;
  esac
done
shift $((OPTIND - 1))

[[ $# -ge 1 ]] || { echo "Usage: $0 [OPTIONS] [user@]<host>"; exit 1; }

# Parse user@host
ARG="$1"
if [[ "${ARG}" == *@* ]]; then
  SSH_USER="${ARG%%@*}"
  TARGET_HOST="${ARG##*@}"
else
  TARGET_HOST="${ARG}"
  read -rp "Username on ${TARGET_HOST}: " SSH_USER
fi

# ── Ensure sshpass is available ───────────────────────────────────────────────
if ! command -v sshpass &>/dev/null; then
  echo "==> Installing sshpass..."
  if command -v brew &>/dev/null; then
    brew install hudochenkov/sshpass/sshpass
  elif command -v apt-get &>/dev/null; then
    sudo apt-get install -y sshpass
  else
    die "sshpass not found and no supported package manager (brew/apt). Install it manually."
  fi
fi

# ── Prompt for password ───────────────────────────────────────────────────────
echo ""
echo "Bootstrap: ${SSH_USER}@${TARGET_HOST}"
echo ""
read -rsp "Password for ${SSH_USER}@${TARGET_HOST}: " SSH_PASS
echo ""

# ── Step 1: Wait for SSH ──────────────────────────────────────────────────────
echo ""
echo "==> Checking SSH on ${TARGET_HOST}..."

if ! ssh_reachable; then
  echo ""
  echo "    SSH is not reachable yet. On the console of ${TARGET_HOST}, run:"
  echo ""
  echo "        sudo systemctl start ssh"
  echo ""
  echo "    If SSH is not installed:"
  echo ""
  echo "        sudo apt-get install -y openssh-server && sudo systemctl enable --now ssh"
  echo ""
  echo "    Waiting for SSH to come up (Ctrl-C to abort)..."
  echo ""

  until ssh_reachable; do
    printf "."
    sleep 5
  done
  echo ""
  echo "==> SSH is up."
fi

# ── Step 2: Passwordless sudo ─────────────────────────────────────────────────
echo "==> Setting up passwordless sudo for ${SSH_USER}..."
ssh_run "echo '${SSH_PASS}' | sudo -S bash -c \"
  echo '${SSH_USER} ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/${SSH_USER}
  chmod 440 /etc/sudoers.d/${SSH_USER}
  visudo -c -f /etc/sudoers.d/${SSH_USER}
\""
echo "    Done."

# ── Step 3: Static IP (optional) ──────────────────────────────────────────────
echo ""
read -rp "Set a static IP? [y/N] " SET_STATIC
if [[ "$(echo "${SET_STATIC}" | tr '[:upper:]' '[:lower:]')" == "y" ]]; then
  if [[ -z "${STATIC_IP}" ]]; then
    read -rp "Static IP to assign (e.g. 192.168.1.12): " STATIC_IP
  fi
  if [[ -n "${STATIC_IP}" ]]; then
    "${SCRIPT_DIR}/set-static-ip.sh" \
      -u "${SSH_USER}" \
      ${SSH_KEY:+-k "${SSH_KEY}"} \
      -i "${STATIC_IP}" \
      "${TARGET_HOST}"
    echo "==> Switching to ${STATIC_IP} for remaining steps."
    TARGET_HOST="${STATIC_IP}"
  else
    echo "    No IP given, skipping."
  fi
fi

# ── Step 4: Install personal SSH key so ansible can connect ───────────────────
echo ""
echo "==> Copying personal SSH key to ${SSH_USER}@${TARGET_HOST}..."
SSH_PUB_KEY=""
for _candidate in ~/.ssh/id_ed25519.pub ~/.ssh/id_rsa.pub ~/.ssh/id_ecdsa.pub; do
  if [[ -f "${_candidate}" ]]; then
    SSH_PUB_KEY="${_candidate}"
    break
  fi
done
if [[ -n "${SSH_PUB_KEY}" ]]; then
  sshpass -p "${SSH_PASS}" ssh-copy-id \
    -o StrictHostKeyChecking=accept-new \
    -i "${SSH_PUB_KEY}" \
    ${SSH_KEY:+-i "${SSH_KEY}"} \
    "${SSH_USER}@${TARGET_HOST}" 2>&1 | grep -v "^$" || true
  echo "    Done."
else
  echo "    No personal public key found, skipping."
fi

# ── Step 5: Bootstrap ansible user ────────────────────────────────────────────
echo ""
echo "==> Running bootstrap-ansible-user..."
"${SCRIPT_DIR}/bootstrap-ansible-user.sh" \
  -u "${SSH_USER}" \
  ${SSH_KEY:+-k "${SSH_KEY}"} \
  "${TARGET_HOST}"

echo ""
echo "==> Bootstrap complete. ${TARGET_HOST} is ready for Ansible."
