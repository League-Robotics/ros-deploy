#!/usr/bin/env bash
# scripts/bootstrap-ansible-user.sh
# ──────────────────────────────────────────────────────────────────────────────
# Bootstrap the Ansible service account on one or more freshly provisioned hosts.
#
# What this script does:
#   1. Runs `dotconfig key load ansible` to decrypt the ansible private key
#      from dotconfig into config/files/ansible (mode 0600).
#   2. Invokes playbooks/bootstrap.yml using YOUR personal SSH credentials
#      to create the ansible user and install its public key on the targets.
#
# After this script completes, all subsequent Ansible runs use the ansible
# account automatically (configured in ansible.cfg + group_vars/all.yml).
#
# Prerequisites:
#   - dotconfig installed and `dotconfig key gen ansible` already run.
#   - SOPS age key available (SOPS_AGE_KEY_FILE or SOPS_AGE_KEY set).
#   - Your personal SSH access to the target(s) is working.
#   - ansible and the ansible.posix collection installed locally.
#
# Usage:
#   ./scripts/bootstrap-ansible-user.sh [OPTIONS] [HOST ...]
#
# Options:
#   -u USER   SSH login user for the initial connection  (default: $USER)
#   -k KEY    Path to your personal SSH private key      (default: ~/.ssh/id_rsa)
#   -i INV    Ansible inventory file  (default: inventory/hosts.ini)
#   -K        Prompt for the remote sudo password
#   -h        Show this help and exit
#
# Note: ansible.cfg sets private_key_file to the ansible service key.  This
# script automatically overrides that with your personal key (ANSIBLE_PRIVATE_KEY_FILE)
# so the initial connection uses your own credentials, not the ansible key.
#
# Examples:
#   # Bootstrap all hosts in inventory as 'ubuntu', prompt for sudo password
#   ./scripts/bootstrap-ansible-user.sh -u ubuntu -K
#
#   # Bootstrap a single ad-hoc host
#   ./scripts/bootstrap-ansible-user.sh -u ubuntu -K 192.168.1.20
#
#   # Bootstrap the host 'agony.local' with a specific personal key
#   ./scripts/bootstrap-ansible-user.sh -u eric -k ~/.ssh/id_ed25519 -K agony.local
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Defaults ──────────────────────────────────────────────────────────────────
BOOTSTRAP_USER="${USER}"
BOOTSTRAP_KEY=""
INVENTORY="${REPO_ROOT}/inventory/hosts.ini"
ASK_BECOME=""
EXTRA_HOSTS=()

# Detect a sensible default personal key (overridden by -k).
for _candidate in ~/.ssh/id_ed25519 ~/.ssh/id_rsa ~/.ssh/id_ecdsa; do
  if [[ -f "${_candidate}" ]]; then
    BOOTSTRAP_KEY="${_candidate}"
    break
  fi
done

# ── Argument parsing ──────────────────────────────────────────────────────────
usage() {
  grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \{0,1\}//'
  exit 0
}

while getopts ":u:k:i:Kh" opt; do
  case "${opt}" in
    u) BOOTSTRAP_USER="${OPTARG}" ;;
    k) BOOTSTRAP_KEY="${OPTARG}" ;;
    i) INVENTORY="${OPTARG}" ;;
    K) ASK_BECOME="--ask-become-pass" ;;
    h) usage ;;
    :) echo "Option -${OPTARG} requires an argument." >&2; exit 1 ;;
    \?) echo "Unknown option: -${OPTARG}" >&2; usage ;;
  esac
done
shift $((OPTIND - 1))
EXTRA_HOSTS=("$@")

cd "${REPO_ROOT}"

# ── Step 1: Decrypt the ansible private key ───────────────────────────────────
echo "==> Decrypting ansible keypair from dotconfig..."
dotconfig key load ansible || {
  # dotconfig refuses to overwrite an existing file — that's fine if it's already there.
  if [[ -f "${REPO_ROOT}/config/files/ansible" ]]; then
    echo "    (key already present, skipping decrypt)"
  else
    echo "ERROR: dotconfig key load failed and key is missing." >&2
    exit 1
  fi
}

ANSIBLE_PUB_KEY="${REPO_ROOT}/config/keys/ansible.pub"
ANSIBLE_PRIV_KEY="${REPO_ROOT}/config/files/ansible"

if [[ ! -f "${ANSIBLE_PUB_KEY}" ]]; then
  echo "ERROR: ${ANSIBLE_PUB_KEY} not found." >&2
  echo "       Run 'dotconfig key gen ansible' to create the keypair first." >&2
  exit 1
fi

# ── Step 2: Build ansible-playbook arguments ──────────────────────────────────
# Override the ansible.cfg private_key_file so we connect with the personal key,
# not the ansible service key (which doesn't exist yet on a new host).
if [[ -n "${BOOTSTRAP_KEY}" ]]; then
  export ANSIBLE_PRIVATE_KEY_FILE="${BOOTSTRAP_KEY}"
  echo "==> Using personal SSH key: ${BOOTSTRAP_KEY}"
else
  unset ANSIBLE_PRIVATE_KEY_FILE
  echo "==> No personal key found — relying on SSH agent."
fi

PLAYBOOK_ARGS=(
  playbooks/bootstrap.yml
  -u "${BOOTSTRAP_USER}"
  -e "bootstrap_user=${BOOTSTRAP_USER}"
)

if [[ -n "${ASK_BECOME}" ]]; then
  PLAYBOOK_ARGS+=("${ASK_BECOME}")
fi

# If ad-hoc hosts were given, build a comma-separated inline inventory.
# Ansible requires a trailing comma for single-host inline inventories.
if [[ ${#EXTRA_HOSTS[@]} -gt 0 ]]; then
  HOST_LIST="$(IFS=,; echo "${EXTRA_HOSTS[*]}"),"
  PLAYBOOK_ARGS+=(-i "${HOST_LIST}")
else
  PLAYBOOK_ARGS+=(-i "${INVENTORY}")
fi

# ── Step 3: Run the bootstrap playbook ───────────────────────────────────────
echo "==> Bootstrapping ansible user as '${BOOTSTRAP_USER}'..."
ansible-playbook "${PLAYBOOK_ARGS[@]}"

# ── Step 4: Verify ────────────────────────────────────────────────────────────
echo ""
echo "==> Bootstrap complete. Verifying connectivity as ansible user..."

if [[ ${#EXTRA_HOSTS[@]} -gt 0 ]]; then
  PING_INV="$(IFS=,; echo "${EXTRA_HOSTS[*]}"),"
else
  PING_INV="${INVENTORY}"
fi

ansible all \
  -i "${PING_INV}" \
  --private-key "${ANSIBLE_PRIV_KEY}" \
  -u ansible \
  -m ping \
  && echo "==> All hosts reachable as ansible. You're ready to run playbooks." \
  || echo "WARNING: ping failed — check SSH connectivity and try manually."
