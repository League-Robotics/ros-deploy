#!/usr/bin/env python3
"""Live joystick viewer over rosbridge — verify a controller works on any node.

On start it finds every sensor_msgs/Joy topic the gateway can see (e.g. the
`/<host>/joy/<N>` topics published by the joy role), lets you pick one, then
shows a live text UI of its axes and buttons. Plug a controller into any fleet
machine running the joy publisher and watch it move here.

    uv run joydump.py                      # pick from a menu
    uv run joydump.py --topic /agony/joy/0  # skip the menu
    uv run joydump.py --host vidar          # a different gateway
"""

from __future__ import annotations

import argparse
import sys
import threading
import time

import roslibpy
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import _conn

JOY_TYPE = "sensor_msgs/Joy"


def choose_topic(console: Console, ros: roslibpy.Ros, preset: str) -> str:
    """Resolve the Joy topic to view: the preset, or an interactive menu."""
    topics = _conn.topics_for_type(ros, JOY_TYPE)
    # Fall back to a name match in case a publisher mislabels the type.
    if not topics:
        topics = [t for t, _ in _conn.list_topics(ros) if "joy" in t.lower()]

    if preset:
        if preset in topics:
            return preset
        console.print(
            f"[yellow]Warning:[/yellow] {preset} isn't currently advertised as "
            f"{JOY_TYPE}; trying it anyway."
        )
        return preset

    if not topics:
        console.print(
            f"[red]No {JOY_TYPE} topics found.[/red]\n"
            "Plug a controller into a node running the joy publisher, then rerun.\n"
            "(Check with: [cyan]uv run list_topics.py --filter joy[/cyan])"
        )
        sys.exit(1)

    console.print(f"[bold]Joystick topics:[/bold]")
    for i, t in enumerate(topics):
        console.print(f"  [cyan]{i}[/cyan]  {t}")
    if len(topics) == 1:
        console.print(f"[dim]auto-selecting the only topic[/dim]")
        return topics[0]

    while True:
        raw = console.input("Select a topic [0]: ").strip() or "0"
        if raw.isdigit() and 0 <= int(raw) < len(topics):
            return topics[int(raw)]
        console.print("[red]invalid choice[/red]")


def _bar(value: float, width: int = 31) -> Text:
    """A centered horizontal gauge for an axis value in [-1, 1]."""
    value = max(-1.0, min(1.0, value))
    mid = width // 2
    pos = int(round((value + 1.0) / 2.0 * (width - 1)))
    cells = []
    for i in range(width):
        if i == pos:
            cells.append("[bold cyan]●[/bold cyan]")
        elif i == mid:
            cells.append("[dim]┃[/dim]")
        elif min(pos, mid) < i < max(pos, mid):
            cells.append("[cyan]─[/cyan]")
        else:
            cells.append("[dim]·[/dim]")
    return Text.from_markup("".join(cells))


def render(topic: str, state: dict) -> Panel:
    msg = state.get("msg")
    rate = state.get("rate", 0.0)
    age = time.time() - state["last"] if state.get("last") else None

    if msg is None:
        body: object = Align.center(
            Text("waiting for messages…", style="yellow"), vertical="middle"
        )
        status = "[yellow]no data yet[/yellow]"
    else:
        axes = msg.get("axes", [])
        buttons = msg.get("buttons", [])

        atab = Table.grid(padding=(0, 1))
        atab.add_column(justify="right", style="dim")
        atab.add_column()
        atab.add_column(justify="right")
        for i, v in enumerate(axes):
            atab.add_row(f"axis {i}", _bar(float(v)), f"{float(v):+.3f}")

        btext = Text()
        for i, b in enumerate(buttons):
            on = bool(b)
            btext.append(f" {i:>2} ", style="black on green" if on else "green")
            btext.append(" ")
        if not buttons:
            btext = Text("(no buttons)", style="dim")

        body = Group(
            atab if axes else Text("(no axes)", style="dim"),
            Text(""),
            Text("buttons:", style="bold"),
            btext,
        )
        fresh = age is not None and age < 1.0
        status = (
            f"[{'green' if fresh else 'red'}]{rate:5.1f} Hz[/]  "
            f"last msg {age * 1000:4.0f} ms ago"
            if age is not None else "[yellow]no data[/yellow]"
        )

    return Panel(
        body,
        title=f"[bold cyan]{topic}[/bold cyan]",
        subtitle=status + "   [dim]Ctrl-C to quit[/dim]",
        border_style="cyan",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _conn.add_conn_args(parser)
    parser.add_argument(
        "--topic", default="", help="Joy topic to view (default: choose from a menu)"
    )
    args = parser.parse_args()

    console = Console()
    ros = _conn.connect(args.host, args.port)
    topic = choose_topic(console, ros, args.topic)

    lock = threading.Lock()
    state: dict = {"msg": None, "last": None, "rate": 0.0, "count": 0, "first": None}

    def on_message(message: dict) -> None:
        now = time.time()
        with lock:
            state["msg"] = message
            state["count"] += 1
            if state["first"] is None:
                state["first"] = now
            elif now > state["first"]:
                state["rate"] = (state["count"] - 1) / (now - state["first"])
            state["last"] = now

    listener = roslibpy.Topic(ros, topic, JOY_TYPE)
    listener.subscribe(on_message)

    try:
        with Live(console=console, screen=True, auto_refresh=False) as live:
            while True:
                with lock:
                    snapshot = dict(state)
                live.update(render(topic, snapshot), refresh=True)
                time.sleep(0.05)  # ~20 fps
    except KeyboardInterrupt:
        pass
    finally:
        listener.unsubscribe()
        ros.terminate()


if __name__ == "__main__":
    main()
