#!/usr/bin/env python3
"""
fleet-portal/serve.py — a tiny LAN web server that hands other agents everything
they need to connect to the ROS fleet WITHOUT ever telling them a username or a
password.

Run it on any machine that has this repo checked out and the decrypted Ansible
key present (`dotconfig key load ansible`). It binds to 0.0.0.0 so any host on
the LAN can reach it:

    ./fleet-portal/serve.py                 # http://<this-machine>:8770/
    ./fleet-portal/serve.py --port 9000
    ./fleet-portal/serve.py --host 192.168.1.11

What it serves
--------------
  GET /                     Human/agent-readable connection portal (index.html)
  GET /fleet.json           Machine-readable inventory of the ROS hosts
  GET /ssh_config           Ready-to-use ~/.ssh/config block (aliases + user + key)
  GET /connect.sh           One-shot installer: fetches the key + config, wires
                            up SSH so `ssh agony` (etc.) just works
  GET /keys/fleet_id        The SSH PRIVATE KEY that authorizes the shared `ros`
                            account. This is the ONE secret; it is delivered over
                            HTTP on the LAN so an agent never types a credential.
  GET /keys/fleet_id.pub    The matching public key

Why this satisfies "no usernames or passwords"
-----------------------------------------------
  * SSH  — the agent authenticates with the key this server hands out, logging in
           as the shared `ros` account. The username lives in the SSH config
           alias; the agent just runs `ssh agony`. No password is ever entered.
  * ROS  — the fleet exposes rosbridge WebSocket gateways (ws://<host>:9090) that
           require NO authentication at all. An agent pubs/subs ROS topics by
           opening a WebSocket; there are no ROS credentials to know.

SECURITY NOTE: anything reachable on the LAN can download the private key from
/keys/fleet_id. That is the explicit intent here — this is a lab-network broker,
not a public service. Do not expose the port beyond the trusted LAN. Bind it to
a specific interface with --host if you want to narrow reachability.
"""

from __future__ import annotations

import argparse
import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PORTAL_DIR = Path(__file__).resolve().parent

# The private key this broker distributes. It is the decrypted Ansible/`ros`
# key produced by `dotconfig key load ansible`. Agents that hold it can log in
# as the shared `ros` account on every fleet node.
PRIVATE_KEY = REPO_ROOT / "config" / "files" / "ansible"
PUBLIC_KEY = REPO_ROOT / "config" / "keys" / "ansible.pub"

# The SSH login used on the nodes. The whole point of the portal is that the
# agent never has to know or type this — it is baked into the SSH config alias.
#
# It MUST be the account the served key authorizes. The key handed out below is
# config/files/ansible, whose public half (config/keys/ansible.pub) is deployed
# to the `ansible` account on every node by roles/ansible_user. The `ros`
# account only accepts Eric's personal key (eric.pub), which is NOT in this repo
# — so logging in as `ansible` is what actually works with this key. The
# `ansible` account has NOPASSWD sudo, so it can do anything on the node.
SSH_LOGIN_USER = "ansible"

# ROS 2 DDS domain shared by the whole fleet (group_vars/all.yml: ros_domain_id).
ROS_DOMAIN_ID = 42

# The five hosts the portal advertises. Addresses come from inventory/hosts.ini.
# `rosbridge` marks hosts that run the ws://<host>:9090 JSON/WebSocket gateway
# (inventory/hosts.ini [rosbridge_hosts]); those need NO ROS credentials at all.
FLEET = [
    {
        "name": "agony",
        "ip": "192.168.1.11",
        "role": "amd64 workstation (Ubuntu 24.04, ROS 2 Kilted)",
        "rosbridge": True,
        "notes": "rosbridge gateway; also the usual host for running rviz/rqt viewers.",
    },
    {
        "name": "torture",
        "ip": "192.168.1.12",
        "role": "amd64 workstation (Ubuntu 22.04, ROS 2 Humble)",
        "rosbridge": True,
        "notes": "rosbridge gateway.",
    },
    {
        "name": "ali",
        "ip": "192.168.1.143",
        "role": "Raspberry Pi (ROS 2 Kilted)",
        "rosbridge": True,
        "notes": "rosbridge gateway; VNC desktop on :1.",
    },
    {
        "name": "nepr",
        "ip": "192.168.1.146",
        "role": "Raspberry Pi 4 (ROS 2 Kilted)",
        "rosbridge": False,
        "notes": "Drives a robot over a serial port. No rosbridge — reach it over SSH.",
    },
    {
        "name": "vidar",
        "ip": "192.168.1.144",
        "role": "Raspberry Pi 5 (ROS 2 Kilted)",
        "rosbridge": False,
        "notes": "Two CSI cameras; MJPEG at http://vidar:8080/. No rosbridge.",
    },
    {
        "name": "baldur",
        "ip": "192.168.1.152",
        "role": "Raspberry Pi 4 (ROS 2 Kilted)",
        "rosbridge": False,
        "notes": "X-drive robot: xdrive-driver on /cmd_vel -> REV Hub. See /wiki/xdrive-teleop.md.",
    },
    {
        "name": "golem",
        "ip": "192.168.1.173",
        "role": "Raspberry Pi 4 (ROS 2 Kilted)",
        "rosbridge": False,
        "notes": "Operator station: gamepad -> joy-to-twist -> /cmd_vel (drives baldur).",
    },
]

# The repo's wiki (docs/wiki/*.md) — the source of truth the League hub also
# publishes at http://robots.jointheleague.org/subsystems/ros-deploy/.
# Served raw here so LAN agents can curl pages without GitHub or a browser.
WIKI_DIR = REPO_ROOT / "docs" / "wiki"
HUB_URL = "http://robots.jointheleague.org/subsystems/ros-deploy/"


def _server_ip() -> str:
    """Best-effort LAN IP of this machine, for building absolute URLs."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def build_ssh_config() -> str:
    """A drop-in ~/.ssh/config block: one alias per host, user + key pre-filled.

    The agent that installs this never learns the login name or a password — it
    just runs `ssh agony`. The key path points at where connect.sh saves it.
    """
    lines = [
        "# ROS fleet — generated by fleet-portal. Aliases pre-fill the login user",
        "# and key, so an agent connects with `ssh <name>` and never sees a",
        f"# username or password. Login user is `{SSH_LOGIN_USER}` (the shared ROS account).",
        "",
    ]
    for h in FLEET:
        lines += [
            f"Host {h['name']}",
            f"    HostName {h['ip']}",
            f"    User {SSH_LOGIN_USER}",
            "    IdentityFile ~/.ssh/fleet_id",
            "    IdentitiesOnly yes",
            "    StrictHostKeyChecking accept-new",
            "",
        ]
    return "\n".join(lines)


def build_connect_sh(base_url: str) -> str:
    """A self-contained installer the agent curls and pipes to sh."""
    return f"""#!/usr/bin/env sh
# fleet-portal connect.sh — wire up SSH access to the ROS fleet.
# Run:  curl -fsSL {base_url}/connect.sh | sh
# Afterwards `ssh agony`, `ssh vidar`, etc. work with no username or password.
set -eu

BASE="{base_url}"
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

echo "Fetching fleet key..."
curl -fsSL "$BASE/keys/fleet_id" -o "$HOME/.ssh/fleet_id"
chmod 600 "$HOME/.ssh/fleet_id"

echo "Installing SSH config aliases..."
curl -fsSL "$BASE/ssh_config" -o "$HOME/.ssh/fleet_config"
# Include the fleet block from the main config if not already wired in.
if ! grep -q "Include ~/.ssh/fleet_config" "$HOME/.ssh/config" 2>/dev/null; then
    printf 'Include ~/.ssh/fleet_config\\n' | cat - "$HOME/.ssh/config" 2>/dev/null > "$HOME/.ssh/config.new" || \\
        printf 'Include ~/.ssh/fleet_config\\n' > "$HOME/.ssh/config.new"
    mv "$HOME/.ssh/config.new" "$HOME/.ssh/config"
    chmod 600 "$HOME/.ssh/config"
fi

echo ""
echo "Done. Try:  ssh agony hostname"
echo ""
echo "IMPORTANT: a non-interactive SSH session does NOT set ROS_DOMAIN_ID, so"
echo "ros2 CLI defaults to domain 0 and sees nothing. Prefix ROS commands with it:"
echo "  ssh agony 'ROS_DOMAIN_ID={ROS_DOMAIN_ID} bash -lc \\"source /opt/ros/*/setup.bash && ros2 topic list\\"'"
echo ""
echo "ROS over WebSocket (no ROS credentials): ws://agony:9090  ws://torture:9090  ws://ali:9090"
echo "The fleet's ROS_DOMAIN_ID is {ROS_DOMAIN_ID}."
"""


def render_index(base_url: str) -> bytes:
    tpl = (PORTAL_DIR / "index.html").read_text(encoding="utf-8")
    rows = []
    for h in FLEET:
        bridge = (
            f'<code>ws://{h["name"]}:9090</code>'
            if h["rosbridge"]
            else '<span class="muted">SSH only</span>'
        )
        rows.append(
            "<tr>"
            f'<td><code>{h["name"]}</code></td>'
            f'<td><code>{h["ip"]}</code></td>'
            f'<td>{h["role"]}</td>'
            f"<td>{bridge}</td>"
            f'<td class="muted">{h["notes"]}</td>'
            "</tr>"
        )
    html = (
        tpl.replace("{{BASE_URL}}", base_url)
        .replace("{{FLEET_ROWS}}", "\n".join(rows))
        .replace("{{ROS_DOMAIN_ID}}", str(ROS_DOMAIN_ID))
        .replace("{{SSH_USER}}", SSH_LOGIN_USER)
    )
    return html.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "FleetPortal/1.0"

    def _base_url(self) -> str:
        host = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
        return f"http://{host}"

    def _send(self, body: bytes, ctype: str, *, download: str | None = None, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(body)

    def _text(self, path: Path, ctype: str, download: str | None = None) -> None:
        if not path.is_file():
            self._send(
                f"Not available: {path.name}. Run `dotconfig key load ansible` "
                "in the repo, then restart the portal.".encode(),
                "text/plain; charset=utf-8",
                status=503,
            )
            return
        self._send(path.read_bytes(), ctype, download=download)

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        route = self.path.split("?", 1)[0].rstrip("/") or "/"
        base = self._base_url()

        if route == "/":
            self._send(render_index(base), "text/html; charset=utf-8")
        elif route == "/fleet.json":
            payload = {
                "ros_domain_id": ROS_DOMAIN_ID,
                "ssh_user": SSH_LOGIN_USER,
                "connect_script": f"{base}/connect.sh",
                "ssh_config": f"{base}/ssh_config",
                "private_key": f"{base}/keys/fleet_id",
                "wiki": {p.stem: f"{base}/wiki/{p.name}" for p in sorted(WIKI_DIR.glob("[!_]*.md"))},
                "wiki_hub": HUB_URL,
                "hosts": FLEET,
            }
            self._send(json.dumps(payload, indent=2).encode(), "application/json")
        elif route == "/wiki":
            pages = "\n".join(f"- {base}/wiki/{p.name}" for p in sorted(WIKI_DIR.glob("[!_]*.md")))
            self._send(
                f"# Fleet wiki (rendered: {HUB_URL})\n\n{pages}\n".encode(),
                "text/markdown; charset=utf-8",
            )
        elif route.startswith("/wiki/"):
            name = route.removeprefix("/wiki/")
            page = (WIKI_DIR / name).resolve()
            # Only plain .md files that actually live in WIKI_DIR (no traversal).
            if page.parent == WIKI_DIR.resolve() and page.suffix == ".md" and page.is_file():
                self._send(page.read_bytes(), "text/markdown; charset=utf-8")
            else:
                self._send(b"Not found\n", "text/plain; charset=utf-8", status=404)
        elif route == "/ssh_config":
            self._send(build_ssh_config().encode(), "text/plain; charset=utf-8", download="fleet_config")
        elif route == "/connect.sh":
            self._send(build_connect_sh(base).encode(), "text/x-shellscript; charset=utf-8")
        elif route == "/keys/fleet_id":
            self._text(PRIVATE_KEY, "application/octet-stream", download="fleet_id")
        elif route == "/keys/fleet_id.pub":
            self._text(PUBLIC_KEY, "text/plain; charset=utf-8", download="fleet_id.pub")
        else:
            self._send(b"Not found\n", "text/plain; charset=utf-8", status=404)

    def log_message(self, fmt: str, *args) -> None:  # quieter, one line per hit
        print(f"[portal] {self.address_string()} {fmt % args}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Serve the ROS fleet connection portal on the LAN.")
    ap.add_argument("--host", default="0.0.0.0", help="Interface to bind (default: 0.0.0.0 = all LAN)")
    ap.add_argument("--port", type=int, default=8770, help="Port to listen on (default: 8770)")
    args = ap.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    shown = _server_ip() if args.host == "0.0.0.0" else args.host
    print("ROS fleet portal is up.")
    print(f"  Open:      http://{shown}:{args.port}/")
    print(f"  Agents:    curl -fsSL http://{shown}:{args.port}/connect.sh | sh")
    if not PRIVATE_KEY.is_file():
        print("  WARNING:   config/files/ansible is missing — run `dotconfig key load ansible`")
        print("             or /keys/fleet_id will return 503 until you do.")
    print("  Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
