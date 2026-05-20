# syntax=docker/dockerfile:1
# ──────────────────────────────────────────────────────────────────────────────
# ROS Noetic (ROS 1) — multi-architecture image
# Supported platforms: linux/amd64  linux/arm64  linux/arm/v7
#
# Build:
#   docker buildx build \
#     --platform linux/amd64,linux/arm64,linux/arm/v7 \
#     -t your-registry/ros-noetic:latest \
#     --push docker/noetic/
#
# Run (joins the host ROS network via host networking):
#   docker run --rm --network host \
#     -e ROS_MASTER_URI=http://<master-ip>:11311 \
#     -e ROS_IP=$(hostname -I | awk '{print $1}') \
#     your-registry/ros-noetic:latest roscore
# ──────────────────────────────────────────────────────────────────────────────
FROM ros:noetic-ros-base

ARG DEBIAN_FRONTEND=noninteractive

# Install common ROS tools and networking utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-rosdep \
    python3-rosinstall \
    python3-rosinstall-generator \
    python3-wstool \
    python3-catkin-tools \
    build-essential \
    net-tools \
    iputils-ping \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Initialise rosdep
RUN rosdep init && rosdep update

# Copy entrypoint that sources ROS and honours ROS_MASTER_URI / ROS_IP
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV ROS_DISTRO=noetic

ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
