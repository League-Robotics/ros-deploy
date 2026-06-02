#!/usr/bin/env bash
# scripts/ros-mac-shell.sh
# ──────────────────────────────────────────────────────────────────────────────
# Open a ROS 2 shell on the macOS ROS VM (`macvm`) with GUI forwarding to XQuartz.
#
# The UTM-hosted `macvm` is a normal Ubuntu ROS node on the LAN.  This helper
# just SSHes in with X11 forwarding (-Y) so rviz2 / rqt windows render on the
# Mac through XQuartz.  ROS 2 is auto-sourced by /etc/profile.d/ros_env.sh
# (deployed by the `ros` role), so `ros2`, `rviz2`, etc. are on PATH at login.
#
# The target IP is read from inventory/hosts.ini (the `ansible_host=` of the
# macvm entry); falls back to `macvm.local` (mDNS).  Override with -H.
#
# Usage:
#   ./scripts/ros-mac-shell.sh [OPTIONS] [-- COMMAND ...]
#
# Options:
#   -H HOST   Target host/IP            (default: macvm's ansible_host, else macvm.local)
#   -u USER   SSH login user            (default: ansible)
#   -k KEY    SSH private key           (default: config/files/ansible)
#   -n        No X11 forwarding (plain shell, no XQuartz)
#   -h        Show this help and exit
#
# Examples:
#   ./scripts/ros-mac-shell.sh                       # interactive GUI-ready shell
#   ./scripts/ros-mac-shell.sh -- rviz2              # launch rviz2 directly
#   ./scripts/ros-mac-shell.sh -- ros2 topic list    # one-shot command
#   ./scripts/ros-mac-shell.sh -n                    # headless shell, skip XQuartz
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Defaults ──────────────────────────────────────────────────────────────────
HOST_ALIAS="macvm"
SSH_USER="ansible"
SSH_KEY="${REPO_ROOT}/config/files/ansible"
INVENTORY="${REPO_ROOT}/inventory/hosts.ini"
TARGET=""
USE_X11=1

usage() { grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \{0,1\}//'; exit 0; }
die()   { echo "ERROR: $*" >&2; exit 1; }

# ── Argument parsing ──────────────────────────────────────────────────────────
while getopts ":H:u:k:nh" opt; do
  case "${opt}" in
    H) TARGET="${OPTARG}" ;;
    u) SSH_USER="${OPTARG}" ;;
    k) SSH_KEY="${OPTARG}" ;;
    n) USE_X11=0 ;;
    h) usage ;;
    :) die "Option -${OPTARG} requires an argument." ;;
    \?) die "Unknown option: -${OPTARG}" ;;
  esac
done
shift $((OPTIND - 1))
# Remaining args (after an optional `--`) are the remote command.
REMOTE_CMD=("$@")

# ── Resolve target address ────────────────────────────────────────────────────
if [[ -z "${TARGET}" ]]; then
  # Pull `ansible_host=<ip>` from the macvm line in the inventory, if present.
  TARGET="$(awk -v h="${HOST_ALIAS}" '
    $1==h { for (i=1;i<=NF;i++) if ($i ~ /^ansible_host=/) { sub(/^ansible_host=/,"",$i); print $i; exit } }
  ' "${INVENTORY}" 2>/dev/null || true)"
fi
[[ -n "${TARGET}" ]] || TARGET="${HOST_ALIAS}.local"

# ── Build SSH args ────────────────────────────────────────────────────────────
SSH_OPTS=(-o StrictHostKeyChecking=accept-new)
[[ -f "${SSH_KEY}" ]] && SSH_OPTS+=(-i "${SSH_KEY}")

# ── Ensure XQuartz / DISPLAY for X11 forwarding ───────────────────────────────
if [[ "${USE_X11}" -eq 1 ]]; then
  open -a XQuartz 2>/dev/null || echo "WARNING: could not launch XQuartz (is it installed?)" >&2
  # XQuartz publishes its display socket to launchd; pick it up if our shell
  # doesn't already have DISPLAY set (common when not started from an xterm).
  if [[ -z "${DISPLAY:-}" ]]; then
    for _ in $(seq 1 10); do
      DISPLAY="$(launchctl getenv DISPLAY 2>/dev/null || true)"
      [[ -n "${DISPLAY}" ]] && break
      sleep 1
    done
    export DISPLAY
  fi
  [[ -n "${DISPLAY:-}" ]] || echo "WARNING: DISPLAY is empty; GUI forwarding may not work. Try opening XQuartz manually." >&2
  SSH_OPTS+=(-Y)
fi

echo "==> Connecting to ${SSH_USER}@${TARGET}${USE_X11:+ (X11 → XQuartz)}"
exec ssh "${SSH_OPTS[@]}" "${SSH_USER}@${TARGET}" ${REMOTE_CMD[@]+"${REMOTE_CMD[@]}"}
