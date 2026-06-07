#!/usr/bin/env python3
"""List every ROS topic visible through the rosbridge gateway, with its type.

Like `ros2 topic list -t`, but over the JSON/WebSocket bridge — no ROS install.

    uv run list_topics.py                 # all topics on agony:9090
    uv run list_topics.py --filter joy    # only topics whose name contains 'joy'
    uv run list_topics.py --host vidar     # a different gateway
"""

from __future__ import annotations

import argparse

from rich.console import Console
from rich.table import Table

import _conn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _conn.add_conn_args(parser)
    parser.add_argument(
        "--filter", default="", metavar="TEXT",
        help="only show topics whose name contains TEXT",
    )
    args = parser.parse_args()

    console = Console()
    ros = _conn.connect(args.host, args.port)
    try:
        topics = _conn.list_topics(ros)
    finally:
        ros.terminate()

    if args.filter:
        needle = args.filter.lower()
        topics = [(t, ty) for t, ty in topics if needle in t.lower()]

    table = Table(title=f"ROS topics via ws://{args.host}:{args.port}")
    table.add_column("Topic", style="cyan", no_wrap=True)
    table.add_column("Type", style="green")
    for topic, type_name in topics:
        table.add_row(topic, type_name or "[dim]?[/dim]")

    console.print(table)
    console.print(f"[dim]{len(topics)} topic(s)[/dim]")


if __name__ == "__main__":
    main()
