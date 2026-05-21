# syntax=docker/dockerfile:1
# ──────────────────────────────────────────────────────────────────────────────
# ROS 2 Kilted Kaiju — multi-architecture image
# Supported platforms: linux/amd64  linux/arm64
#
# Build:
#   docker buildx build \
#     --platform linux/amd64,linux/arm64 \
#     -t your-registry/ros-kilted:latest \
#     --push docker/kilted/
#
# Run (joins the host ROS 2 domain via host networking):
#   docker run --rm --network host \
#     -e ROS_DOMAIN_ID=42 \
#     your-registry/ros-kilted:latest ros2 run demo_nodes_cpp talker
# ──────────────────────────────────────────────────────────────────────────────
FROM ros:kilted-ros-base

ARG DEBIAN_FRONTEND=noninteractive

# Install common ROS 2 tools and networking utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-dev-tools \
    net-tools \
    iputils-ping \
    iproute2 \
    ros-kilted-demo-nodes-cpp \
    ros-kilted-demo-nodes-py \
    && rm -rf /var/lib/apt/lists/*

# Initialise rosdep
RUN rosdep init && rosdep update

# Copy entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV ROS_DISTRO=kilted

ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
