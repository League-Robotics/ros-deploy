---
title: Using the rosbridge Gateway
blurb: Connect non-ROS clients (Python or browser) to the fleet over WebSocket — where the bridge runs and how to talk to it.
order: 35
tags: [ros, rosbridge, integration]
---

# Using the rosbridge Gateway

rosbridge lets you read and write the fleet's ROS 2 topics from code that has **no
ROS install at all** — plain JSON over a WebSocket. Use it from a laptop, a browser,
a script, or any agent that can open a WebSocket. This page covers everything
specific to *our* setup; for the generic protocol and client libraries it links out.

## Where it is (our environment)

| Thing | Value | Notes |
|-------|-------|-------|
| **Gateway host** | `agony` | The only host running the bridge by default. |
| **URL** | `ws://agony:9090` | rosbridge v2 JSON protocol over WebSocket. |
| **Port** | `9090` | `rosbridge_port` default. |
| **DDS domain** | `42` | The bridge only sees topics on domain 42 (the fleet domain). |
| **systemd service** | `rosbridge` | Auto-starts on boot, restarts on failure. |
| **Auth** | **none** | Any client that connects can pub/sub any topic and call any service. Lab-only — see [Security](#security). |
| **Env var overrides** | `ROSBRIDGE_HOST`, `ROSBRIDGE_PORT` | Honored by the demo clients in `test/`; default to `agony` / `9090`. |

Confirm it's up before debugging a client:

```bash
ssh agony 'systemctl status rosbridge'
```

## Talk to it from Python

`roslibpy` is pure Python/WebSocket — no ROS needed (`pip install roslibpy`):

```python
import roslibpy
ros = roslibpy.Ros(host='agony', port=9090); ros.run()

# read: ROS → client
roslibpy.Topic(ros, '/agony/joy/0', 'sensor_msgs/Joy').subscribe(
    lambda m: print(m['axes']))

# write: client → ROS
cmd = roslibpy.Topic(ros, '/cmd_vel', 'geometry_msgs/Twist')
cmd.publish(roslibpy.Message({'linear': {'x': 0.5}, 'angular': {'z': 0.2}}))
```

You must supply the message **type** string (e.g. `sensor_msgs/Joy`) — discover it
with rosapi introspection below.

## Talk to it from a browser

Same idea with [`roslib.js`](https://github.com/RobotWebTools/roslibjs) against
`ws://agony:9090`:

```js
const ros = new ROSLIB.Ros({ url: 'ws://agony:9090' });
new ROSLIB.Topic({ ros, name: '/heartbeat', messageType: 'std_msgs/String' })
  .subscribe(m => console.log(m.data));
```

## Discovering what's available (rosapi)

The bridge ships `rosapi`, so clients can introspect without ROS. Service calls in
`roslibpy` block and return the result:

```python
topics = roslibpy.Service(ros, '/rosapi/topics', 'rosapi/Topics').call(
    roslibpy.ServiceRequest())            # -> {'topics': [...], 'types': [...]}
t = roslibpy.Service(ros, '/rosapi/topic_type', 'rosapi/TopicType').call(
    roslibpy.ServiceRequest({'topic': '/agony/joy/0'}))   # -> {'type': 'sensor_msgs/Joy'}
```

Useful rosapi services: `/rosapi/topics`, `/rosapi/topics_for_type`,
`/rosapi/topic_type`.

## Ready-made clients (fastest start)

The repo's [`test/`](https://github.com/League-Robotics/ros-deploy/blob/HEAD/test/README.md)
directory has working roslibpy clients you can copy or run directly. They already
encode the connection conventions (`_conn.py` handles host/port/env-var defaults):

```bash
cd test && uv sync                     # installs roslibpy + rich
uv run list_topics.py                  # every visible topic + type
uv run echo.py /heartbeat              # dump a topic (type auto-detected)
uv run joydump.py                      # live joystick viewer (pick a topic)
```

Override the target with `--host/--port` or `ROSBRIDGE_HOST`/`ROSBRIDGE_PORT`.

## Topic naming conventions

- Per-host sources are namespaced by hostname, e.g. joystick axes/buttons publish on
  `/agony/joy/0` (host / `joy` / controller index).
- Fleet-wide topics aren't namespaced, e.g. `/heartbeat` (liveness, host names) and
  the `/chatter` demo talker.
- When in doubt, list topics with `list_topics.py` rather than guessing.

## Security

The bridge binds `0.0.0.0` with **no authentication** — anyone who can reach
`agony:9090` has full pub/sub/service access. That's acceptable inside the lab LAN.
If untrusted clients could reach the host, set `rosbridge_address` to a specific LAN
interface in host_vars and/or front it with a curated gateway. The bridge can only
surface topics it sees over DDS — nodes must be interoperating on **domain 42**.

## Enabling the bridge on another host

It runs on `agony` today. To add it elsewhere, set in
`inventory/host_vars/<host>.yml`:

```yaml
install_rosbridge: true
# rosbridge_port: 9090        # default
# rosbridge_address: 0.0.0.0  # default — set a LAN IP to restrict reach
```

Then deploy:

```bash
ansible-playbook playbooks/rosbridge.yml --limit <host>
```

## Reference

- In-repo: [README → rosbridge gateway](https://github.com/League-Robotics/ros-deploy/blob/HEAD/README.md#rosbridge-gateway-non-ros-clients),
  role [`roles/rosbridge`](https://github.com/League-Robotics/ros-deploy/blob/HEAD/roles/rosbridge),
  clients [`test/`](https://github.com/League-Robotics/ros-deploy/blob/HEAD/test/README.md).
- External: [rosbridge_suite / protocol](https://github.com/RobotWebTools/rosbridge_suite),
  [roslibpy docs](https://roslibpy.readthedocs.io),
  [roslib.js](https://github.com/RobotWebTools/roslibjs).
