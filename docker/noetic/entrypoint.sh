#!/usr/bin/env bash
# entrypoint.sh for ros-noetic container
# Sources ROS and applies environment variables before running the command.
set -e

# Source ROS Noetic
# shellcheck source=/dev/null
source /opt/ros/noetic/setup.bash

# Workspace (optional — mounted at runtime)
if [ -f /root/workspace/devel/setup.bash ]; then
  # shellcheck source=/dev/null
  source /root/workspace/devel/setup.bash
fi

# ROS_MASTER_URI and ROS_IP are injected via env_file or -e flags at runtime.
# Print them so the user can verify networking at startup.
echo "---------------------------------------------------"
echo " ROS Noetic container starting"
echo " ROS_MASTER_URI = ${ROS_MASTER_URI:-<not set>}"
echo " ROS_IP         = ${ROS_IP:-<not set>}"
echo "---------------------------------------------------"

exec "$@"
