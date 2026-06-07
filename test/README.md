# rosbridge demo clients

Small Python programs that talk to the fleet over the **rosbridge gateway**
(`rosbridge_server` on agony, see [`../roles/rosbridge`](../roles/rosbridge)).
They use [`roslibpy`](https://roslibpy.readthedocs.io) — pure JSON/WebSocket,
**no ROS install required**. This is the "non-ROS client" path documented in the
main [README](../README.md#rosbridge-gateway-non-ros-clients).

## Setup (uv)

```bash
cd test
uv sync          # creates .venv and installs roslibpy + rich
```

The gateway defaults to `agony:9090`. Override per command with `--host/--port`,
or globally with `ROSBRIDGE_HOST` / `ROSBRIDGE_PORT`.

## Programs

**`list_topics.py`** — list every visible topic and its type (like `ros2 topic list -t`):

```bash
uv run list_topics.py                 # everything
uv run list_topics.py --filter joy    # just joystick topics
```

**`echo.py`** — dump messages from a topic (type auto-detected, like `ros2 topic echo`):

```bash
uv run echo.py /chatter        # demo talker
uv run echo.py /heartbeat      # fleet heartbeat (host names)
uv run echo.py /chatter -n 5   # stop after 5 messages
```

**`joydump.py`** — live joystick viewer (text UI). Finds all `sensor_msgs/Joy`
topics, lets you pick one, then shows axes (as gauges) and buttons updating live —
so you can confirm a controller works when plugged into another machine:

```bash
uv run joydump.py                       # pick from a menu
uv run joydump.py --topic /agony/joy/0  # skip the menu
```

It shows a live Hz reading and the age of the last message, so a frozen or
disconnected controller is obvious at a glance.
