"""Shared connection helpers for the rosbridge demo clients.

Every script talks to the fleet's rosbridge gateway (rosbridge_server running on
agony, see ../roles/rosbridge). Nothing here needs ROS installed — it's all the
JSON/WebSocket rosbridge protocol via roslibpy.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import roslibpy

# Default target is the gateway host (agony:9090). Override per-run with
# --host/--port, or globally with the ROSBRIDGE_HOST / ROSBRIDGE_PORT env vars.
DEFAULT_HOST = os.environ.get("ROSBRIDGE_HOST", "agony")
DEFAULT_PORT = int(os.environ.get("ROSBRIDGE_PORT", "9090"))


def add_conn_args(parser: argparse.ArgumentParser) -> None:
    """Add the standard --host/--port options to an argparse parser."""
    parser.add_argument(
        "--host", default=DEFAULT_HOST,
        help=f"rosbridge host (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"rosbridge port (default: {DEFAULT_PORT})",
    )


def connect(host: str, port: int, timeout: float = 10.0) -> roslibpy.Ros:
    """Connect to rosbridge and return a running Ros client (exits on failure)."""
    ros = roslibpy.Ros(host=host, port=port)
    ros.run()  # non-blocking: starts the event loop in a background thread
    deadline = time.time() + timeout
    while not ros.is_connected and time.time() < deadline:
        time.sleep(0.1)
    if not ros.is_connected:
        print(
            f"ERROR: could not connect to rosbridge at ws://{host}:{port} "
            f"within {timeout:.0f}s.\n"
            f"  • is the gateway up?  ssh {host} 'systemctl status rosbridge'\n"
            f"  • try a different host/port with --host/--port",
            file=sys.stderr,
        )
        ros.terminate()
        sys.exit(1)
    return ros


# ── rosapi introspection (blocking service calls) ─────────────────────────────
# roslibpy service calls block and return the result when no callback is given.

def list_topics(ros: roslibpy.Ros) -> list[tuple[str, str]]:
    """Return [(topic, type), ...] for every topic the gateway can see."""
    res = roslibpy.Service(ros, "/rosapi/topics", "rosapi/Topics").call(
        roslibpy.ServiceRequest()
    )
    topics = res.get("topics", [])
    types = res.get("types", [])
    # types is positionally aligned with topics in rosapi/Topics responses.
    paired = list(zip(topics, types)) if len(types) == len(topics) else [
        (t, "") for t in topics
    ]
    return sorted(paired)


def topics_for_type(ros: roslibpy.Ros, type_name: str) -> list[str]:
    """Return the topics currently published with the given message type."""
    res = roslibpy.Service(ros, "/rosapi/topics_for_type", "rosapi/TopicsForType").call(
        roslibpy.ServiceRequest({"type": type_name})
    )
    return sorted(res.get("topics", []))


def topic_type(ros: roslibpy.Ros, topic: str) -> str:
    """Return the message type of a topic (empty string if unknown)."""
    res = roslibpy.Service(ros, "/rosapi/topic_type", "rosapi/TopicType").call(
        roslibpy.ServiceRequest({"topic": topic})
    )
    return res.get("type", "")
