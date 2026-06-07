#!/usr/bin/env python3
"""Publish a synthetic sensor_msgs/Joy so you can test joydump without hardware.

This is the *write* direction over rosbridge (client → ROS): it advertises a Joy
topic and publishes moving axes + cycling buttons. Run it in one terminal and
`joydump.py` in another to see the viewer working end-to-end.

    uv run fake_joy.py                       # publishes /test/joy at 20 Hz
    uv run fake_joy.py --topic /agony/joy/0  # masquerade as a real joy topic
"""

from __future__ import annotations

import argparse
import math
import time

import roslibpy

import _conn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _conn.add_conn_args(parser)
    parser.add_argument("--topic", default="/test/joy", help="topic to publish on")
    parser.add_argument("--rate", type=float, default=20.0, help="publish rate (Hz)")
    args = parser.parse_args()

    ros = _conn.connect(args.host, args.port)
    pub = roslibpy.Topic(ros, args.topic, "sensor_msgs/Joy")
    pub.advertise()
    print(f"publishing fake Joy on {args.topic} at {args.rate:.0f} Hz — Ctrl-C to stop")

    period = 1.0 / args.rate
    i = 0
    try:
        while True:
            t = i * period
            axes = [math.sin(t), math.cos(t), math.sin(t * 0.5)]
            buttons = [int(i % 40 < 20), 0, int(i % 60 < 30), 0]
            pub.publish(roslibpy.Message({"axes": axes, "buttons": buttons}))
            i += 1
            time.sleep(period)
    except KeyboardInterrupt:
        pass
    finally:
        pub.unadvertise()
        ros.terminate()


if __name__ == "__main__":
    main()
