#!/usr/bin/env bash
# entrypoint.sh for ros-kilted container
# Sources ROS 2 and applies environment variables before running the command.
set -e

# Source ROS 2 Kilted
# shellcheck source=/dev/null
source /opt/ros/kilted/setup.bash

# Workspace (optional — mounted at runtime)
if [ -f /root/workspace/install/setup.bash ]; then
  # shellcheck source=/dev/null
  source /root/workspace/install/setup.bash
fi

# ROS_DOMAIN_ID is injected via env_file or -e flag at runtime.
echo "---------------------------------------------------"
echo " ROS 2 Kilted container starting"
echo " ROS_DOMAIN_ID      = ${ROS_DOMAIN_ID:-<not set>}"
echo " ROS_LOCALHOST_ONLY = ${ROS_LOCALHOST_ONLY:-0}"
echo "---------------------------------------------------"

exec "$@"
