"""Heartbeat node: announce this host and list every peer seen on /heartbeat."""

import socket
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

TOPIC = 'heartbeat'
PUBLISH_PERIOD_S = 1.0
REPORT_PERIOD_S = 5.0
PEER_TIMEOUT_S = 10.0


def _sanitize(name: str) -> str:
    return ''.join(c if c.isalnum() or c == '_' else '_' for c in name)


class HeartbeatNode(Node):
    def __init__(self) -> None:
        self.hostname = socket.gethostname()
        super().__init__(f'heartbeat_{_sanitize(self.hostname)}')

        self.publisher = self.create_publisher(String, TOPIC, 10)
        self.subscription = self.create_subscription(
            String, TOPIC, self._on_heartbeat, 10
        )

        # peer_hostname -> last-seen monotonic timestamp
        self.peers: dict[str, float] = {}
        self.last_reported: frozenset[str] = frozenset()

        self.create_timer(PUBLISH_PERIOD_S, self._publish)
        self.create_timer(REPORT_PERIOD_S, self._report)

        self.get_logger().info(
            f"heartbeat up on host '{self.hostname}', "
            f"publishing to /{TOPIC} every {PUBLISH_PERIOD_S:.0f}s"
        )

    def _publish(self) -> None:
        msg = String()
        msg.data = self.hostname
        self.publisher.publish(msg)

    def _on_heartbeat(self, msg: String) -> None:
        name = msg.data.strip()
        if not name or name == self.hostname:
            return
        self.peers[name] = time.monotonic()

    def _report(self) -> None:
        now = time.monotonic()
        active = {
            name for name, seen in self.peers.items()
            if now - seen <= PEER_TIMEOUT_S
        }
        # Drop expired peers so the dict doesn't grow forever.
        self.peers = {n: self.peers[n] for n in active}

        snapshot = frozenset(active)
        if snapshot == self.last_reported:
            return
        self.last_reported = snapshot

        if not active:
            self.get_logger().info('no peers visible yet')
        else:
            peer_list = ', '.join(sorted(active))
            self.get_logger().info(
                f'I see {len(active)} peer(s): {peer_list}'
            )


def main(args=None) -> None:
    rclpy.init(args=args)
    node = HeartbeatNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
