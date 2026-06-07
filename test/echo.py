#!/usr/bin/env python3
"""Dump messages from a ROS topic, like `ros2 topic echo`, over rosbridge.

The message type is auto-detected via rosapi, so you only pass the topic name.

    uv run echo.py /chatter            # the demo talker (std_msgs/String)
    uv run echo.py /heartbeat          # fleet heartbeat (host names)
    uv run echo.py /chatter -n 5       # stop after 5 messages
    uv run echo.py /foo --type std_msgs/String   # force a type if rosapi can't tell
"""

from __future__ import annotations

import argparse
import json
import threading

import roslibpy
from rich.console import Console
from rich.syntax import Syntax

import _conn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _conn.add_conn_args(parser)
    parser.add_argument("topic", help="topic to echo, e.g. /chatter")
    parser.add_argument(
        "-n", "--count", type=int, default=0,
        help="stop after N messages (default: run until Ctrl-C)",
    )
    parser.add_argument(
        "--type", default="", metavar="MSGTYPE",
        help="message type override (default: auto-detect via rosapi)",
    )
    args = parser.parse_args()

    console = Console()
    ros = _conn.connect(args.host, args.port)

    msg_type = args.type or _conn.topic_type(ros, args.topic)
    if not msg_type:
        console.print(
            f"[red]Could not determine the type of {args.topic}.[/red] "
            f"Is anything publishing it? Pass --type to force one."
        )
        ros.terminate()
        return

    console.print(
        f"[dim]echoing[/dim] [cyan]{args.topic}[/cyan] "
        f"[dim]([/dim][green]{msg_type}[/green][dim]) — Ctrl-C to stop[/dim]"
    )

    done = threading.Event()
    seen = {"n": 0}
    listener = roslibpy.Topic(ros, args.topic, msg_type)

    def on_message(message: dict) -> None:
        if done.is_set():  # ignore stragglers between the limit and unsubscribe
            return
        seen["n"] += 1
        console.rule(f"[dim]#{seen['n']}[/dim]", style="dim")
        console.print(Syntax(json.dumps(message, indent=2), "json", background_color="default"))
        if args.count and seen["n"] >= args.count:
            done.set()

    listener.subscribe(on_message)
    try:
        done.wait()  # interruptible by Ctrl-C; set when count reached
    except KeyboardInterrupt:
        pass
    finally:
        listener.unsubscribe()
        ros.terminate()
        console.print(f"\n[dim]done — {seen['n']} message(s)[/dim]")


if __name__ == "__main__":
    main()
