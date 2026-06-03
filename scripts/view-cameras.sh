#!/usr/bin/env bash
# scripts/view-cameras.sh
# ──────────────────────────────────────────────────────────────────────────────
# View ROS 2 camera streams on your local Mac by running an image viewer on a
# remote node (default: agony) and forwarding its window over SSH X11 to XQuartz.
#
# The remote node only needs to be on the ROS network (same ROS_DOMAIN_ID) — it
# subscribes to the camera topics published by other nodes (e.g. vidar's CSI
# cameras). Nothing is captured on the remote node itself.
#
# The target IP is read from inventory/hosts.ini (the host's ansible_host),
# falling back to <host>.local. Run this FROM the Mac (XQuartz must be present).
#
# Usage:
#   ./scripts/view-cameras.sh [OPTIONS]
#
# Options:
#   -H HOST   Remote node to run the viewer on   (default: agony)
#   -u USER   SSH login user                      (default: ansible)
#   -k KEY    SSH private key                     (default: config/files/ansible)
#   -t TOPIC  View this image topic directly via image_view
#             (default: open rqt_image_view and pick the topic from the dropdown)
#   -h        Show this help and exit
#
# Examples:
#   ./scripts/view-cameras.sh                                   # rqt_image_view on agony
#   ./scripts/view-cameras.sh -t /camera0/camera/image_raw      # view camera0 directly
#   ./scripts/view-cameras.sh -H torture -u eric                # different node/user
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

HOST_ALIAS="agony"
SSH_USER="ansible"
SSH_KEY="${REPO_ROOT}/config/files/ansible"
INVENTORY="${REPO_ROOT}/inventory/hosts.ini"
TOPIC=""

usage() { grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \{0,1\}//'; exit 0; }
die()   { echo "ERROR: $*" >&2; exit 1; }

while getopts ":H:u:k:t:h" opt; do
  case "${opt}" in
    H) HOST_ALIAS="${OPTARG}" ;;
    u) SSH_USER="${OPTARG}" ;;
    k) SSH_KEY="${OPTARG}" ;;
    t) TOPIC="${OPTARG}" ;;
    h) usage ;;
    :) die "Option -${OPTARG} requires an argument." ;;
    \?) die "Unknown option: -${OPTARG}" ;;
  esac
done

# ── Resolve target address from inventory, else mDNS ──────────────────────────
TARGET="$(awk -v h="${HOST_ALIAS}" '
  $1==h { for (i=1;i<=NF;i++) if ($i ~ /^ansible_host=/) { sub(/^ansible_host=/,"",$i); print $i; exit } }
' "${INVENTORY}" 2>/dev/null || true)"
[[ -n "${TARGET}" ]] || TARGET="${HOST_ALIAS}.local"

# ── Ensure XQuartz is running and DISPLAY is set for X11 forwarding ───────────
open -a XQuartz 2>/dev/null || echo "WARNING: could not launch XQuartz (is it installed?)" >&2
if [[ -z "${DISPLAY:-}" ]]; then
  for _ in $(seq 1 10); do
    DISPLAY="$(launchctl getenv DISPLAY 2>/dev/null || true)"
    [[ -n "${DISPLAY}" ]] && break
    sleep 1
  done
  export DISPLAY
fi
[[ -n "${DISPLAY:-}" ]] || echo "WARNING: DISPLAY is empty; GUI forwarding may not work." >&2

# ── Build the remote viewer command ───────────────────────────────────────────
# Login shell (-l) sources /etc/profile.d/ros_env.sh → ROS 2 + ROS_DOMAIN_ID.
if [[ -n "${TOPIC}" ]]; then
  # image_view on a single topic; request compressed transport if the compressed
  # topic flavour is implied (works for raw too).
  REMOTE="ros2 run image_view image_view --ros-args -r image:=${TOPIC}"
else
  REMOTE="ros2 run rqt_image_view rqt_image_view"
fi

SSH_OPTS=(-Y -o StrictHostKeyChecking=accept-new)
[[ -f "${SSH_KEY}" ]] && SSH_OPTS+=(-i "${SSH_KEY}")

echo "==> Launching viewer on ${SSH_USER}@${TARGET} (X11 → XQuartz)"
echo "    ${REMOTE}"
[[ -n "${TOPIC}" ]] || echo "    (pick a topic, e.g. /camera0/camera/image_raw/compressed, from the dropdown)"
exec ssh "${SSH_OPTS[@]}" "${SSH_USER}@${TARGET}" "bash -lc '${REMOTE}'"
