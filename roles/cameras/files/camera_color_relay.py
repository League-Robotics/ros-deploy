#!/usr/bin/env python3
# roles/cameras/files/camera_color_relay.py
# Managed by Ansible — do not edit by hand.
#
# Works around a camera_ros bug on Pi 5 / Ubuntu: it publishes image pixels
# with the red and blue channels in the opposite order to the encoding label it
# stamps (e.g. data is BGR but labelled rgb8), so every faithful consumer shows
# red<->blue swapped. This node subscribes to each camera's image_raw
# (BEST_EFFORT, so it never adds back-pressure to camera_ros's reliable, depth-1
# publisher), swaps R and B so the data matches the label, and republishes on
# <ns>/camera/image_color (RELIABLE, so web_video_server's subscriber matches).
#
# Usage: camera_color_relay.py <in_topic> [<in_topic> ...]
#   each <in_topic> like /camera0/camera/image_raw -> /camera0/camera/image_color
import sys
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data, QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import Image


class ColorRelay(Node):
    def __init__(self, in_topics):
        super().__init__("camera_color_relay")
        pub_qos = QoSProfile(depth=5, reliability=ReliabilityPolicy.RELIABLE,
                             history=HistoryPolicy.KEEP_LAST)
        self.pubs = {}
        for t in in_topics:
            out = t.rsplit("/", 1)[0] + "/image_color"
            self.pubs[t] = self.create_publisher(Image, out, pub_qos)
            self.create_subscription(Image, t, self._make_cb(t), qos_profile_sensor_data)
            self.get_logger().info(f"relaying {t} -> {out} (R/B swapped)")

    def _make_cb(self, topic):
        pub = self.pubs[topic]

        def cb(msg):
            if msg.height == 0 or msg.width == 0:
                return
            # Rows are padded to msg.step bytes, which is NOT always
            # width*channels — libcamera aligns each row (observed on vidar:
            # 1456x1088 rgb8 has step=4416, while width*3 is only 4368, i.e. 48
            # bytes of padding per row). Deriving the channel count from
            # len(data)//(h*w) and reshaping to (h, w, ch) therefore fails with
            # "cannot reshape array of size ...". Index via step instead, and
            # leave the padding untouched.
            ch = msg.step // msg.width
            if ch < 3:
                pub.publish(msg)
                return
            buf = np.frombuffer(bytes(msg.data), dtype=np.uint8)
            if buf.size < msg.height * msg.step:
                return  # truncated frame; drop rather than crash the relay
            rows = buf[: msg.height * msg.step].reshape(msg.height, msg.step)
            px = rows[:, : msg.width * ch].reshape(msg.height, msg.width, ch)
            out = rows.copy()
            opx = out[:, : msg.width * ch].reshape(msg.height, msg.width, ch)
            opx[:, :, 0] = px[:, :, 2]
            opx[:, :, 2] = px[:, :, 0]
            msg.data = out.tobytes()
            pub.publish(msg)

        return cb


def main():
    rclpy.init()
    node = ColorRelay(sys.argv[1:])
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
